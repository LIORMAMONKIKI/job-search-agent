"""Enrich Notion Companies DB with Careers URL + ATS provider.

Strategy (cheap-first):
  1. Probe common ATS slug patterns (Greenhouse, Lever, Ashby, Workable,
     Comeet, SmartRecruiters) via HEAD requests. Free + fast.
  2. If no match, call Claude with web search to find the careers page.

Dry-run only: writes a CSV + markdown summary for review. No Notion writes yet.

Usage:
  python enrich.py [--tier T3]
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from anthropic import Anthropic
from notion_client import Client

from config import NOTION_TOKEN, ANTHROPIC_API_KEY, COMPANIES_DATA_SOURCE_ID
from notion_io import _extract_title, _extract_text, _extract_select, _tier_full_name


# Each ATS exposes a JSON endpoint that 404s for non-existent companies AND
# returns a structured payload with job listings for real ones. We probe these
# instead of frontend URLs, which often return 200 for invalid slugs (Ashby is
# a JS SPA and 200s for literally anything).
#
# Each provider: (label, api_url_template, validation_fn, careers_url_template)
ATS_PROVIDERS = [
    (
        "Greenhouse",
        "https://boards-api.greenhouse.io/v1/boards/{}/jobs",
        lambda d: isinstance(d, dict) and "jobs" in d and isinstance(d["jobs"], list),
        "https://boards.greenhouse.io/{}",
    ),
    (
        "Lever",
        "https://api.lever.co/v0/postings/{}?mode=json",
        lambda d: isinstance(d, list),
        "https://jobs.lever.co/{}",
    ),
    (
        "Ashby",
        "https://api.ashbyhq.com/posting-api/job-board/{}",
        lambda d: isinstance(d, dict) and ("jobs" in d or "postings" in d),
        "https://jobs.ashbyhq.com/{}",
    ),
    (
        "Workable",
        "https://apply.workable.com/api/v1/widget/accounts/{}",
        lambda d: isinstance(d, dict) and "name" in d,
        "https://apply.workable.com/{}",
    ),
    (
        "SmartRecruiters",
        "https://api.smartrecruiters.com/v1/companies/{}/postings",
        lambda d: isinstance(d, dict) and ("content" in d or "postings" in d),
        "https://jobs.smartrecruiters.com/{}",
    ),
    (
        "Teamtailor",
        "https://{}.teamtailor.com/jobs.json",
        lambda d: isinstance(d, dict) and "jobs" in d,
        "https://{}.teamtailor.com/jobs",
    ),
]

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def slugify(name):
    """Best-effort company-name → URL slug."""
    s = name.lower().strip()
    # strip parens and their content (e.g., "Meta (Tel Aviv)" → "meta")
    s = re.sub(r"\s*\(.*?\)\s*", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def slug_variants(name):
    """Generate a few slug variants per company. Avoid first-word-only
    truncation since it produces garbage slugs (e.g. "D-ID" → "d", "Hour One"
    → "hour", "Bria AI" → "bria")."""
    base = slugify(name)
    variants = {base, base.replace("-", "")}
    # Strip common corporate suffixes
    for suffix in ["-inc", "-ltd", "-llc", "-corp", "-labs", "-com"]:
        if base.endswith(suffix):
            stripped = base[: -len(suffix)]
            variants.add(stripped)
            variants.add(stripped.replace("-", ""))
    return [v for v in variants if len(v) >= 3]


def probe_one(ats, api_url, validate, careers_template, slug, n_jobs_hint=None):
    """GET an ATS JSON endpoint and validate response shape.
    Returns (ats, careers_url, n_jobs) on success, (None, None, 0) on miss.
    """
    try:
        r = requests.get(
            api_url,
            timeout=6,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        if r.status_code != 200:
            return None, None, 0
        data = r.json()
    except Exception:
        return None, None, 0

    if not validate(data):
        return None, None, 0

    # Count jobs for reporting / quality signal
    n_jobs = 0
    if isinstance(data, list):
        n_jobs = len(data)
    elif isinstance(data, dict):
        for key in ("jobs", "postings", "content"):
            if isinstance(data.get(key), list):
                n_jobs = len(data[key])
                break

    careers_url = careers_template.format(slug)
    return ats, careers_url, n_jobs


def find_ats(company_name):
    """Concurrent JSON-API probes across all ATS × slug combos.
    Returns (ats, careers_url, n_jobs) or (None, None, 0).
    """
    variants = slug_variants(company_name)
    candidates = []
    for ats, api_url_template, validate, careers_template in ATS_PROVIDERS:
        for v in variants:
            candidates.append((ats, api_url_template.format(v), validate, careers_template, v))

    hits = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [
            pool.submit(probe_one, ats, api_url, validate, careers_template, slug)
            for ats, api_url, validate, careers_template, slug in candidates
        ]
        for f in as_completed(futures):
            ats, url, n_jobs = f.result()
            # Require at least one job to count as a real hit. Workable +
            # SmartRecruiters return valid JSON shells for any slug, just with
            # an empty jobs array. 0 jobs ≈ wrong ATS for hiring T3 companies.
            if ats and n_jobs >= 1:
                hits.append((ats, url, n_jobs))

    if not hits:
        return None, None, 0

    # Tie-break: more jobs first, then ATS priority order.
    priority = [a for a, *_ in ATS_PROVIDERS]
    hits.sort(key=lambda h: (-h[2], priority.index(h[0]) if h[0] in priority else 99))
    return hits[0]


def validate_careers_url(url):
    """Quick HTTP GET to verify a URL looks like a real careers page.
    Returns True if url returns 200 AND response contains careers-page markers
    (job listings, ATS embed, /careers paths, etc.)."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        r = requests.get(
            url,
            timeout=8,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
        )
    except Exception:
        return False
    if r.status_code != 200:
        return False
    body = r.text[:50000].lower()
    # Look for any plausible careers-page marker
    markers = [
        "career", "jobs", "open positions", "open roles",
        "we're hiring", "we are hiring", "join us", "join our team",
        "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
        "apply.workable.com", "comeet.co", "smartrecruiters.com",
        "myworkdayjobs.com", "bamboohr.com", "teamtailor.com",
    ]
    return any(m in body for m in markers)


def claude_find_careers(client, company_name, notes, max_retries=1):
    """Ask Claude (no web search) what the careers page URL is, then validate
    the returned URL with an HTTP GET. Cheap (~$0.005 per call), fast (~3s),
    and avoids the 30k tokens/min rate limit that kills web_search at scale.
    Returns (careers_url, ats, error)."""
    system = (
        "You are a research assistant that knows the careers page URLs of "
        "well-known tech companies. Output ONLY a single JSON object — no prose, "
        "no preamble, no markdown fences."
    )
    prompt = (
        f"What is the public careers/jobs page URL for '{company_name}'?\n"
        f"Context (may help disambiguate): {notes}\n\n"
        "Output a single JSON object exactly like:\n"
        '{"careers_url": "https://...", "ats": "Greenhouse"}\n\n'
        "ats must be one of: Greenhouse, Lever, Ashby, Workable, Comeet, "
        "SmartRecruiters, Workday, BambooHR, Teamtailor, Custom, Unknown.\n"
        "Use 'Custom' for self-hosted careers pages with no detectable ATS.\n"
        "If you are not confident or do not know the careers page, output:\n"
        '{"careers_url": null, "ats": null}\n'
        "Don't guess a URL you're not sure about — better to return null."
    )
    resp = None
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=300,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except Exception as e:
            last_err = str(e)
            if "429" in last_err or "rate_limit" in last_err:
                wait = 30 * (attempt + 1)
                print(f"           (rate limit, waiting {wait}s before retry {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            return None, None, f"claude error: {e}"
    if resp is None:
        return None, None, f"claude error after {max_retries} retries: {last_err}"

    text_blocks = [b.text for b in resp.content if hasattr(b, "text")]
    full_text = "\n".join(text_blocks).strip()
    if not full_text:
        return None, None, "no text in claude response"

    matches = re.findall(r"\{[^{}]*\}", full_text, re.DOTALL)
    if not matches:
        return None, None, f"no json found in: {full_text[:200]}"
    try:
        data = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None, None, f"could not parse: {matches[-1][:200]}"

    url = data.get("careers_url")
    ats = data.get("ats")
    if not url:
        return None, None, "claude returned null"

    # Validate that the URL actually resolves and looks like a careers page
    if not validate_careers_url(url):
        return None, None, f"url did not validate: {url}"

    return url, ats, None


def get_companies_by_tier(notion, tier):
    """Fetch all companies in the given tier (e.g. 'T3')."""
    rows = []
    cursor = None
    while True:
        kwargs = {
            "data_source_id": COMPANIES_DATA_SOURCE_ID,
            "page_size": 100,
            "filter": {
                "property": "Tier",
                "select": {"equals": _tier_full_name(tier)},
            },
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        result = notion.data_sources.query(**kwargs)
        rows.extend(result["results"])
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")

    companies = []
    for row in rows:
        props = row["properties"]
        name = _extract_title(props.get("Company", {}))
        if not name:
            continue
        companies.append({
            "id": row["id"],
            "name": name,
            "tier": _extract_select(props.get("Tier", {})),
            "location": _extract_text(props.get("Location", {})),
            "hiring_policy": _extract_select(props.get("Hiring Policy", {})),
            "priority": _extract_select(props.get("Priority", {})),
            "notes": _extract_text(props.get("Notes", {})),
        })
    return companies


def load_existing_results(csv_path):
    """Return dict of company → row from an existing CSV (for resume)."""
    if not os.path.exists(csv_path):
        return {}
    out = {}
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            # Keep rows that have a real result (probe hit, or claude hit)
            if row.get("careers_url") and not row.get("error"):
                out[row["company"]] = row
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", default="T3")
    parser.add_argument("--resume", action="store_true", help="Skip companies already in the output CSV")
    args = parser.parse_args()

    if not NOTION_TOKEN:
        print("ERROR: NOTION_TOKEN missing in .env")
        sys.exit(1)
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY missing in .env")
        sys.exit(1)

    notion = Client(auth=NOTION_TOKEN)
    anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)

    today = date.today().isoformat()
    csv_path = f"enrichment_{args.tier}_{today}.csv"
    existing = load_existing_results(csv_path) if args.resume else {}
    if existing:
        print(f"Resume mode: {len(existing)} companies already enriched in {csv_path}")

    print(f"Fetching {args.tier} companies from Notion...")
    companies = get_companies_by_tier(notion, args.tier)
    print(f"  → {len(companies)} companies\n")

    results = []
    free_hits = 0
    claude_hits = 0
    misses = 0
    claude_calls_made = 0

    for i, c in enumerate(companies, 1):
        name = c["name"]
        # Resume: keep prior successful result without re-running
        if name in existing:
            prev = existing[name]
            results.append({
                **prev,
                "n_jobs": int(prev.get("n_jobs") or 0),
            })
            if prev.get("method") == "claude":
                claude_hits += 1
            else:
                free_hits += 1
            print(f"  [{i:3}/{len(companies)}] {name}  (cached: {prev['ats']})")
            continue

        print(f"  [{i:3}/{len(companies)}] {name}")
        ats, url, n_jobs = find_ats(name)
        method = "probe"
        error = None

        if not ats:
            # Pace Claude calls: web_search responses are ~10-15k tokens each,
            # and Anthropic's rate limit is 30k input tokens / minute.
            # No web_search → tiny token usage, no rate-limit issues.
            # Brief pacing for politeness only.
            if claude_calls_made > 0:
                time.sleep(2)
            print(f"           probe miss, falling back to Claude...")
            url, ats, error = claude_find_careers(anthropic, name, c.get("notes") or "")
            method = "claude"
            claude_calls_made += 1
            if url:
                claude_hits += 1
                print(f"           → [{ats}] {url}")
            else:
                misses += 1
                print(f"           → MISS ({error})")
        else:
            free_hits += 1
            print(f"           → [{ats}] {url}  ({n_jobs} jobs)")

        results.append({
            "company": name,
            "page_id": c["id"],
            "tier": c["tier"],
            "priority": c["priority"],
            "location": c["location"],
            "careers_url": url or "",
            "ats": ats or "",
            "n_jobs": n_jobs,
            "method": method,
            "error": error or "",
            "notes": c.get("notes", ""),
        })

    # Output CSV
    today = date.today().isoformat()
    csv_path = f"enrichment_{args.tier}_{today}.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print(f"\nWrote {csv_path}")

    # Output markdown
    md_path = f"enrichment_{args.tier}_{today}.md"
    with open(md_path, "w") as f:
        f.write(f"# Enrichment dry-run — {args.tier} — {today}\n\n")
        f.write(f"- Total companies: **{len(results)}**\n")
        f.write(f"- Free probe hits: **{free_hits}**\n")
        f.write(f"- Claude fallback hits: **{claude_hits}**\n")
        f.write(f"- Misses: **{misses}**\n\n")
        f.write("| Company | ATS | Jobs | Careers URL | Method |\n")
        f.write("|---|---|---|---|---|\n")
        for r in results:
            url = r["careers_url"] or "—"
            ats = r["ats"] or "—"
            n = r.get("n_jobs", 0)
            f.write(f"| {r['company']} | {ats} | {n} | {url} | {r['method']} |\n")
    print(f"Wrote {md_path}")

    print(f"\n=== Summary: {free_hits} probe / {claude_hits} claude / {misses} miss ===")


if __name__ == "__main__":
    main()
