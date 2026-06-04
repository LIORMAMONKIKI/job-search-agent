"""End-to-end sourcing pipeline.

Flow:
  1. Load all Companies from Notion (name → context).
  2. LinkedIn keyword scrape (every SEARCH_KEYWORDS × SCRAPER_LOCATIONS).
  3. Careers-page scrape (every company with a Careers URL + ATS).
  4. Combine + dedupe by job_url.
  5. Cheap title pre-filter to drop obvious noise.
  6. Per-role: cross-ref company in DB → judge → write to Sourced Roles if pass.
  7. Print digest.

Flags:
  --limit N      stop after judging N roles (smoke testing)
  --skip-linkedin     skip the LinkedIn keyword scrape
  --skip-careers      skip the careers-page scrape
  --tiers T1,T3       only judge roles from companies in these tiers (DB lookup)
"""
import argparse
import re
import sys
import time
from datetime import date

from notion_client import Client

from config import NOTION_TOKEN, ANTHROPIC_API_KEY, COMPANIES_DATA_SOURCE_ID
from notion_io import (
    get_client,
    write_sourced_role,
    get_existing_sourced_jd_links,
    _extract_title,
    _extract_text,
    _extract_select,
)
from scraper import (
    search_all_keywords,
    filter_noise_titles,
)
from careers_scraper import scrape_careers_page
from gmail_scraper import scrape_gmail_job_alerts
from judge import judge_role
from digest import print_daily_digest
from verify_links import verify_links


# ---- Helpers ----------------------------------------------------------------

def normalize_company_name(name):
    """Lowercase + strip parens/suffixes for fuzzy matching."""
    if not name:
        return ""
    s = name.lower().strip()
    s = re.sub(r"\s*\(.*?\)\s*", "", s)  # drop "(Tel Aviv)" etc.
    s = re.sub(r"\b(ltd|inc|llc|corp|labs|the)\b", "", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def load_companies(notion):
    """Pull every Company row from Notion. Return {normalized_name: company_dict}.
    Also return a list of all companies (for the careers-page scrape)."""
    rows = []
    cursor = None
    while True:
        kwargs = {"data_source_id": COMPANIES_DATA_SOURCE_ID, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        result = notion.data_sources.query(**kwargs)
        rows.extend(result["results"])
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")

    by_name = {}
    all_companies = []
    for row in rows:
        props = row["properties"]
        name = _extract_title(props.get("Company", {}))
        if not name:
            continue
        c = {
            "id": row["id"],
            "name": name,
            "tier": _extract_select(props.get("Tier", {})),
            "location": _extract_text(props.get("Location", {})),
            "hiring_policy": _extract_select(props.get("Hiring Policy", {})),
            "priority": _extract_select(props.get("Priority", {})),
            "status": _extract_select(props.get("Status", {})),
            "notes": _extract_text(props.get("Notes", {})),
            "ats": _extract_select(props.get("ATS", {})),
            "careers_url": props.get("Careers URL", {}).get("url"),
        }
        if c["status"] == "Not a Fit":
            continue
        all_companies.append(c)
        by_name[normalize_company_name(name)] = c
    return by_name, all_companies


# ---- Pipeline stages --------------------------------------------------------

def stage_linkedin_scrape():
    print("\n=== Stage 1: LinkedIn keyword scrape ===")
    roles = search_all_keywords()
    print(f"  → {len(roles)} raw roles from LinkedIn")
    return roles


def stage_gmail_scrape():
    print("\n=== Stage 3: Gmail job-alert scrape ===")
    roles = scrape_gmail_job_alerts(verbose=True)
    print(f"  → {len(roles)} roles from Gmail")
    return roles


def stage_verify_existing_links(notion):
    """Stage 0: check every existing active role's JD Link is still alive.
    Dead listings (404/410, LinkedIn redirect to generic page) → Action=Stale.
    Date-based cleanup doesn't work — jobs can be relevant for months. Only
    liveness is a reliable signal."""
    print("\n=== Stage 0: Verify existing roles' JD Links ===")
    try:
        stats = verify_links(notion=notion, verbose=True)
        print(f"  → {stats['marked']} roles marked Stale (dead JD Links)")
    except Exception as e:
        print(f"  ! link-verification stage failed: {e}")


def stage_careers_scrape(all_companies):
    print("\n=== Stage 2: Careers-page scrape ===")
    enriched = [c for c in all_companies if c.get("careers_url") and c.get("ats")]
    print(f"  → scraping {len(enriched)} companies with Careers URL+ATS")

    roles = []
    for i, c in enumerate(enriched, 1):
        try:
            cr = scrape_careers_page(c["careers_url"], c["ats"], company_name=c["name"])
            roles.extend(cr)
            if cr:
                print(f"  [{i:3}/{len(enriched)}] {c['name']:30} +{len(cr)} ({c['ats']})")
        except Exception as e:
            print(f"  [{i:3}/{len(enriched)}] {c['name']:30} ! {str(e)[:60]}")
    print(f"  → {len(roles)} raw roles from careers pages")
    return roles


def _normalize_url(url):
    """Best-effort canonical form so dupes across sources collapse.
    - LinkedIn: extract numeric job_id, return canonical view URL
    - Greenhouse: extract company + job_id
    - General: lowercase host, strip trailing slash, drop tracking params
    Returns None if url is None/empty.
    """
    if not url:
        return None
    s = url.strip().lower()
    # LinkedIn variants → canonical
    m = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", s)
    if m:
        return f"linkedin:{m.group(1)}"
    # Greenhouse boards
    m = re.search(r"greenhouse\.io/(?:boards/)?([a-z0-9_-]+)/jobs/(\d+)", s)
    if m:
        return f"greenhouse:{m.group(1)}:{m.group(2)}"
    # Lever
    m = re.search(r"jobs\.lever\.co/([a-z0-9_-]+)/([a-z0-9-]+)", s)
    if m:
        return f"lever:{m.group(1)}:{m.group(2)}"
    # Ashby
    m = re.search(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)/([a-z0-9-]+)", s)
    if m:
        return f"ashby:{m.group(1)}:{m.group(2)}"
    # Generic cleanup
    s = re.sub(r"\?.*$", "", s)            # drop query string
    s = re.sub(r"#.*$", "", s)            # drop fragment
    s = re.sub(r"/+$", "", s)             # drop trailing slash(es)
    s = re.sub(r"^https?://www\.", "https://", s)
    return s


def dedupe_all(role_lists):
    """Combine and dedupe across an ordered list of role lists. Earlier lists
    win on collision. Dedup by a normalized URL key — same LinkedIn job ID
    across plain URL, /comm/ URL, and email URL all collapse to one row.
    Falls back to (company, title) when no URL is available.
    """
    seen = {}
    for rl in role_lists:
        for r in rl:
            key = _normalize_url(r.get("job_url"))
            if not key:
                co = (r.get("company") or "").strip().lower()
                ti = (r.get("title") or "").strip().lower()
                if not (co and ti):
                    continue
                key = f"name:{co}:{ti}"
            if key not in seen:
                seen[key] = r
    return list(seen.values())


def cross_ref_company(role, companies_by_name):
    """Look up the role's company in the DB by normalized name."""
    norm = normalize_company_name(role.get("company") or "")
    if not norm:
        return None
    # Try exact normalized match first
    if norm in companies_by_name:
        return companies_by_name[norm]
    # Try contains (handles "Lightricks Ltd" → "lightricks")
    for k, v in companies_by_name.items():
        if k and (k in norm or norm in k) and len(k) >= 4:
            return v
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Stop after N judged roles (testing)")
    parser.add_argument("--skip-linkedin", action="store_true")
    parser.add_argument("--skip-careers", action="store_true")
    parser.add_argument("--skip-gmail", action="store_true")
    parser.add_argument("--tiers", default=None, help="Only judge roles from companies in these tiers (e.g. T1,T3)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to Notion")
    args = parser.parse_args()

    if not NOTION_TOKEN or not ANTHROPIC_API_KEY:
        print("ERROR: NOTION_TOKEN or ANTHROPIC_API_KEY missing in .env")
        sys.exit(1)

    notion = get_client()
    print(f"[{date.today().isoformat()}] Sourcing pipeline starting")

    print("\n=== Loading Companies from Notion ===")
    companies_by_name, all_companies = load_companies(notion)
    print(f"  → {len(all_companies)} companies loaded ({len(companies_by_name)} unique normalized names)")

    tier_filter = None
    if args.tiers:
        tier_filter = {t.strip() for t in args.tiers.split(",") if t.strip()}
        print(f"  → tier filter: {tier_filter}")

    # Stage 0: prune dead listings from the active view before scraping new ones.
    if not args.dry_run:
        stage_verify_existing_links(notion)

    linkedin_roles = [] if args.skip_linkedin else stage_linkedin_scrape()
    careers_roles = [] if args.skip_careers else stage_careers_scrape(all_companies)
    gmail_roles = [] if args.skip_gmail else stage_gmail_scrape()

    print(f"\n=== Combining + dedupe ===")
    roles = dedupe_all([careers_roles, linkedin_roles, gmail_roles])
    print(f"  → {len(roles)} unique roles after dedupe")

    print(f"\n=== Title pre-filter ===")
    roles, dropped = filter_noise_titles(roles)
    print(f"  → kept {len(roles)}, dropped {len(dropped)} obvious-noise titles")

    existing_urls = get_existing_sourced_jd_links(notion) if not args.dry_run else set()
    print(f"  → {len(existing_urls)} roles already in Sourced Roles DB (dedup)")

    print(f"\n=== Judging ===")
    if args.limit:
        print(f"  (limit={args.limit} for testing)")
        roles = roles[: args.limit]

    added, skipped, rejected, errors, judged = 0, 0, 0, 0, 0

    for i, role in enumerate(roles, 1):
        jd_link = role.get("job_url")
        if not jd_link:
            continue
        if jd_link in existing_urls:
            skipped += 1
            continue

        company_ctx = cross_ref_company(role, companies_by_name)
        # If we have a tier filter and either no context OR wrong tier, skip
        if tier_filter:
            if not company_ctx:
                continue
            ctier_short = (company_ctx.get("tier") or "").split(" - ")[0]
            if ctier_short not in tier_filter:
                continue

        ctx = company_ctx or {
            "tier": "Unknown",
            "hiring_policy": "Unknown - needs research",
            "notes": "(company not yet in DB)",
        }

        try:
            judgment = judge_role(role, ctx)
            judged += 1
        except Exception as e:
            errs = str(e)
            if "rate_limit" in errs or "429" in errs:
                print(f"  [{i:3}/{len(roles)}] rate-limit, waiting 60s")
                time.sleep(60)
                try:
                    judgment = judge_role(role, ctx)
                    judged += 1
                except Exception as e2:
                    print(f"  [{i:3}/{len(roles)}] ! judge error: {str(e2)[:80]}")
                    errors += 1
                    continue
            else:
                print(f"  [{i:3}/{len(roles)}] ! judge error: {errs[:80]}")
                errors += 1
                continue

        match = judgment.get("skill_match", "?")
        title = (role.get("title") or "")[:50]
        cname = (role.get("company") or "?")[:20]
        print(f"  [{i:3}/{len(roles)}] [{match:6}] {title:50} @ {cname}")

        if match == "Reject":
            rejected += 1
            continue

        if args.dry_run:
            added += 1
            continue

        # Write to Sourced Roles DB
        date_posted = None
        raw_dp = role.get("date_posted")
        if raw_dp:
            s = str(raw_dp).split(" ")[0][:10]
            # Pandas NaN serializes to "nan"; pandas NaT to "NaT". Skip those.
            if s and s.lower() not in ("nan", "nat", "none") and len(s) >= 8:
                date_posted = s

        role_data = {
            "title": role.get("title", ""),
            "jd_link": jd_link,
            "location": role.get("location", "") or "",
            "date_posted": date_posted,
            "skill_match": judgment.get("skill_match"),
            "build_from_tags": judgment.get("build_from_tags", []),
            "visa_likelihood": judgment.get("visa_likelihood"),
            "application_friction": judgment.get("application_friction"),
            "matched_skills": judgment.get("matched_skills", []),
            "gap_skills": judgment.get("gap_skills", []),
            "pitch_angle": judgment.get("pitch_angle", ""),
            "why_surfaced": judgment.get("why_surfaced"),
            "warm_intro": False,
        }
        try:
            write_sourced_role(notion, role_data, (company_ctx or {}).get("id"))
            added += 1
            existing_urls.add(jd_link)
        except Exception as e:
            print(f"      ! write error: {str(e)[:80]}")
            errors += 1

    print("\n" + "=" * 60)
    print(f"Run complete: {added} added, {skipped} deduped, {rejected} rejected, {errors} errors")
    print(f"            {judged} total judged this run")
    print("=" * 60)

    if not args.dry_run:
        print_daily_digest(notion)


if __name__ == "__main__":
    main()
