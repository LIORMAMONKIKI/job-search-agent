"""Bulk-import warm_intros.csv → Notion Contacts DB.

Reads warm_intros.csv (created by the other Claude from LinkedIn export).
For each row:
  - Resolves the Company by name against the Notion Companies DB
  - Creates a Contact page with Name, Role, LinkedIn URL, Company relation,
    Connection Type = "LinkedIn Connection", Status = "To Reach Out",
    Last Contacted = the Connected On date (treated as the most recent
    interaction we have on record).

Idempotent: skips rows whose LinkedIn URL is already present in the Contacts DB.

Usage:
  .venv/bin/python import_warm_intros.py [--dry-run] [--limit N]
"""
import argparse
import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from notion_client import Client

from config import (
    NOTION_TOKEN,
    COMPANIES_DATA_SOURCE_ID,
    CONTACTS_DATA_SOURCE_ID,
)
from notion_io import _extract_title


CSV_PATH = Path(__file__).parent / "warm_intros.csv"


def normalize(name):
    if not name:
        return ""
    s = name.lower().strip()
    s = re.sub(r"\s*\(.*?\)\s*", "", s)  # drop "(Tel Aviv)" etc.
    s = re.sub(r"\b(ltd|inc|llc|corp|labs|the)\b", "", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def fetch_companies_by_name(notion):
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
    out = {}
    for row in rows:
        name = _extract_title(row["properties"].get("Company", {}))
        if name:
            out[normalize(name)] = row["id"]
    return out


def fetch_existing_contact_urls(notion):
    """Set of LinkedIn URLs already in Contacts DB."""
    seen = set()
    cursor = None
    while True:
        kw = {"data_source_id": CONTACTS_DATA_SOURCE_ID, "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        r = notion.data_sources.query(**kw)
        for row in r["results"]:
            url = (row["properties"].get("LinkedIn URL", {}) or {}).get("url")
            if url:
                seen.add(url.strip().rstrip("/").lower())
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    return seen


def parse_connected_date(s):
    """LinkedIn export format: '06 Jan 2026' → ISO date 2026-01-06."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not NOTION_TOKEN:
        print("ERROR: NOTION_TOKEN missing")
        sys.exit(1)
    if not CSV_PATH.exists():
        print(f"ERROR: {CSV_PATH} not found")
        sys.exit(1)

    notion = Client(auth=NOTION_TOKEN)

    print("Loading Companies from Notion (for relation lookup)...")
    co_by_name = fetch_companies_by_name(notion)
    print(f"  {len(co_by_name)} companies indexed")

    print("Loading existing Contact URLs (for dedup)...")
    existing = fetch_existing_contact_urls(notion)
    print(f"  {len(existing)} contacts already in Contacts DB")

    rows = list(csv.DictReader(open(CSV_PATH)))
    if args.limit:
        rows = rows[: args.limit]
    print(f"\nReading {len(rows)} warm intros from {CSV_PATH.name}\n")

    created, skipped_dup, skipped_no_url, skipped_no_company, errors = 0, 0, 0, 0, 0

    for i, r in enumerate(rows, 1):
        first = (r.get("First Name") or "").strip()
        last = (r.get("Last Name") or "").strip()
        name = (first + " " + last).strip() or "(unknown)"
        co_name = (r.get("Company_target") or "").strip()
        position = (r.get("Position") or "").strip()
        url = (r.get("URL") or "").strip()
        connected_on = parse_connected_date(r.get("Connected On"))

        if not url:
            skipped_no_url += 1
            print(f"  [{i:3}/{len(rows)}] SKIP no-url   {name}")
            continue

        url_key = url.rstrip("/").lower()
        if url_key in existing:
            skipped_dup += 1
            print(f"  [{i:3}/{len(rows)}] SKIP dup      {name} ({co_name})")
            continue

        company_page_id = co_by_name.get(normalize(co_name))
        if not company_page_id and co_name:
            skipped_no_company += 1
            print(f"  [{i:3}/{len(rows)}] WARN no-co    {name} @ '{co_name}' — creating without company relation")

        props = {
            "Name": {"title": [{"text": {"content": name[:200]}}]},
            "LinkedIn URL": {"url": url},
            "Connection Type": {"select": {"name": "LinkedIn Connection"}},
            "Status": {"select": {"name": "To Reach Out"}},
        }
        if position:
            props["Role"] = {"rich_text": [{"text": {"content": position[:200]}}]}
        if company_page_id:
            props["Company"] = {"relation": [{"id": company_page_id}]}
        if connected_on:
            props["Last Contacted"] = {"date": {"start": connected_on}}

        if args.dry_run:
            print(f"  [{i:3}/{len(rows)}] DRY  would-create {name} @ {co_name}")
            created += 1
            continue

        try:
            notion.pages.create(
                parent={"data_source_id": CONTACTS_DATA_SOURCE_ID},
                properties=props,
            )
            existing.add(url_key)
            created += 1
            print(f"  [{i:3}/{len(rows)}] OK            {name} @ {co_name}")
        except Exception as e:
            errors += 1
            print(f"  [{i:3}/{len(rows)}] FAIL          {name} — {str(e)[:100]}")

        time.sleep(0.3)  # gentle Notion rate-limit

    print("\n" + "=" * 60)
    print(f"Done. {created} created, {skipped_dup} dup-skipped, "
          f"{skipped_no_url} no-url, {skipped_no_company} no-company-match, {errors} errors")
    print("=" * 60)


if __name__ == "__main__":
    main()
