"""Back-fill existing Sourced Roles with the new judge fields.

Reads Sourced Roles from Notion. For each row WITHOUT a Matched Skills value,
re-judges using only title + company + location (no JD description), then
writes back matched_skills, gap_skills, pitch_angle, and refreshed
skill_match/why_surfaced/etc.

Cheaper + faster than re-scraping: ~$2-3 for ~1000 rows, ~1.5 hours rate-limit-paced.
Quality is slightly thinner than the live pipeline (no JD body) but still useful.

Usage:
  python backfill_judge.py [--limit N] [--dry-run]
"""
import argparse
import sys
import time

from notion_client import Client

from config import (
    NOTION_TOKEN,
    ANTHROPIC_API_KEY,
    COMPANIES_DATA_SOURCE_ID,
    SOURCED_ROLES_DATA_SOURCE_ID,
)
from notion_io import _extract_title, _extract_text, _extract_select
from judge import judge_role


def fetch_companies(notion):
    rows, cursor = [], None
    while True:
        kw = {"data_source_id": COMPANIES_DATA_SOURCE_ID, "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        r = notion.data_sources.query(**kw)
        rows.extend(r["results"])
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    by_id = {}
    for row in rows:
        p = row["properties"]
        by_id[row["id"]] = {
            "id": row["id"],
            "name": _extract_title(p.get("Company", {})),
            "tier": _extract_select(p.get("Tier", {})),
            "hiring_policy": _extract_select(p.get("Hiring Policy", {})),
            "notes": _extract_text(p.get("Notes", {})),
        }
    return by_id


def fetch_roles_needing_backfill(notion):
    """All Sourced Roles where Matched Skills is empty."""
    rows, cursor = [], None
    while True:
        kw = {"data_source_id": SOURCED_ROLES_DATA_SOURCE_ID, "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        r = notion.data_sources.query(**kw)
        rows.extend(r["results"])
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    out = []
    for row in rows:
        p = row["properties"]
        matched = p.get("Matched Skills", {}).get("multi_select", [])
        if matched:
            continue  # already back-filled
        title_items = p.get("Role Title", {}).get("title", [])
        title = "".join(i.get("plain_text", "") for i in title_items)
        company_rel = p.get("Company", {}).get("relation", []) or []
        company_id = company_rel[0]["id"] if company_rel else None
        out.append({
            "id": row["id"],
            "title": title,
            "company_id": company_id,
            "location": _extract_text(p.get("Location", {})),
            "job_url": (p.get("JD Link", {}) or {}).get("url"),
        })
    return out


def write_backfill(notion, page_id, judgment):
    props = {}
    if judgment.get("skill_match"):
        props["Skill-Thread Match"] = {"select": {"name": judgment["skill_match"]}}
    if judgment.get("build_from_tags"):
        props["Build-From Tags"] = {
            "multi_select": [{"name": t[:100]} for t in judgment["build_from_tags"][:10]]
        }
    if judgment.get("visa_likelihood"):
        props["Visa Likelihood"] = {"select": {"name": judgment["visa_likelihood"]}}
    if judgment.get("application_friction"):
        props["Application Friction"] = {"select": {"name": judgment["application_friction"]}}
    if judgment.get("matched_skills"):
        props["Matched Skills"] = {
            "multi_select": [{"name": s[:100]} for s in judgment["matched_skills"][:10]]
        }
    if judgment.get("gap_skills"):
        props["Gap Skills"] = {
            "multi_select": [{"name": s[:100]} for s in judgment["gap_skills"][:10]]
        }
    if judgment.get("pitch_angle"):
        props["Pitch Angle"] = {
            "rich_text": [{"text": {"content": judgment["pitch_angle"][:2000]}}]
        }
    if judgment.get("why_surfaced"):
        props["Why Surfaced"] = {
            "rich_text": [{"text": {"content": judgment["why_surfaced"][:2000]}}]
        }
    notion.pages.update(page_id=page_id, properties=props)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Stop after N roles (for testing)")
    parser.add_argument("--dry-run", action="store_true", help="Skip Notion writes")
    args = parser.parse_args()

    if not NOTION_TOKEN or not ANTHROPIC_API_KEY:
        print("ERROR: NOTION_TOKEN or ANTHROPIC_API_KEY missing")
        sys.exit(1)

    notion = Client(auth=NOTION_TOKEN)

    print("Loading companies from Notion...")
    companies = fetch_companies(notion)
    print(f"  {len(companies)} companies loaded")

    print("Loading Sourced Roles needing back-fill...")
    todo = fetch_roles_needing_backfill(notion)
    print(f"  {len(todo)} roles missing Matched Skills")

    if args.limit:
        todo = todo[: args.limit]
        print(f"  limited to {len(todo)} for this run")

    judged, written, errors = 0, 0, 0
    for i, role in enumerate(todo, 1):
        ctx = companies.get(role.get("company_id")) or {
            "tier": "Unknown",
            "hiring_policy": "Unknown - needs research",
            "notes": "(company not in DB)",
        }
        judge_input = {
            "title": role["title"],
            "company": ctx.get("name", "?"),
            "location": role.get("location", ""),
            "job_url": role.get("job_url", ""),
            "description": "",  # not available in DB — judge from title+location+company context
        }
        try:
            judgment = judge_role(judge_input, ctx)
            judged += 1
        except Exception as e:
            errs = str(e)
            if "rate_limit" in errs or "429" in errs:
                print(f"  [{i:4}/{len(todo)}] rate limit hit — waiting 70s")
                time.sleep(70)
                try:
                    judgment = judge_role(judge_input, ctx)
                    judged += 1
                except Exception as e2:
                    print(f"  [{i:4}/{len(todo)}] FAIL {role['title'][:50]} — {str(e2)[:60]}")
                    errors += 1
                    continue
            else:
                print(f"  [{i:4}/{len(todo)}] FAIL {role['title'][:50]} — {errs[:60]}")
                errors += 1
                continue

        match = judgment.get("skill_match", "?")
        title = (role["title"] or "")[:55]
        n_matched = len(judgment.get("matched_skills") or [])
        n_gap = len(judgment.get("gap_skills") or [])
        co = (ctx.get("name") or "?")[:20]
        print(f"  [{i:4}/{len(todo)}] [{match:6}] {title:55} @ {co:20} m={n_matched} g={n_gap}")

        if args.dry_run:
            continue

        try:
            write_backfill(notion, role["id"], judgment)
            written += 1
        except Exception as e:
            print(f"      write error: {str(e)[:80]}")
            errors += 1

    print("\n" + "=" * 60)
    print(f"Back-fill complete: {judged} judged, {written} written, {errors} errors")
    print("=" * 60)


if __name__ == "__main__":
    main()
