"""Mark outdated Sourced Roles as 'Stale'.

Rules (all must be true):
  - Action == "New"   (you never triaged it)
  - Date Sourced is older than --days N (default 30)

Logic: if you sourced a role 30+ days ago and never marked Apply / Save /
Dismiss, it's almost certainly not interesting. Marking it Stale hides it
from the default dashboard view (which shows New / Apply / Save for Later
by default) without deleting anything — you can re-surface by filtering
Action=Stale.

Usage:
  python cleanup_stale.py [--days 30] [--dry-run]
"""
import argparse
import sys
import time
from datetime import date, timedelta

from notion_client import Client

from config import NOTION_TOKEN, SOURCED_ROLES_DATA_SOURCE_ID


def fetch_stale_candidates(notion, cutoff_iso):
    """Roles with Action='New' AND Date Sourced on-or-before cutoff."""
    rows, cursor = [], None
    while True:
        kw = {
            "data_source_id": SOURCED_ROLES_DATA_SOURCE_ID,
            "page_size": 100,
            "filter": {
                "and": [
                    {"property": "Action", "select": {"equals": "New"}},
                    {"property": "Date Sourced", "date": {"on_or_before": cutoff_iso}},
                ]
            },
        }
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
        title_items = p.get("Role Title", {}).get("title", [])
        title = "".join(i.get("plain_text", "") for i in title_items) or "(no title)"
        date_sourced = (p.get("Date Sourced", {}).get("date") or {}).get("start") or "?"
        out.append({"id": row["id"], "title": title, "date_sourced": date_sourced})
    return out


def mark_stale(notion, page_id):
    notion.pages.update(
        page_id=page_id,
        properties={"Action": {"select": {"name": "Stale"}}},
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30,
                        help="Mark as Stale if sourced this many days ago and still New (default 30)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not NOTION_TOKEN:
        print("ERROR: NOTION_TOKEN missing")
        sys.exit(1)

    notion = Client(auth=NOTION_TOKEN)
    cutoff = date.today() - timedelta(days=args.days)
    cutoff_iso = cutoff.isoformat()

    print(f"Looking for roles with Action='New' AND Date Sourced on-or-before {cutoff_iso}")
    print(f"  (= sourced {args.days}+ days ago and never triaged)")

    todo = fetch_stale_candidates(notion, cutoff_iso)
    print(f"\nFound {len(todo)} candidate roles to mark Stale\n")

    if not todo:
        print("Nothing to clean up.")
        return

    if args.dry_run:
        print("DRY RUN — no writes. First 20 candidates:")
        for r in todo[:20]:
            print(f"  {r['date_sourced']}  {r['title'][:60]}")
        return

    updated, errors = 0, 0
    for i, r in enumerate(todo, 1):
        try:
            mark_stale(notion, r["id"])
            updated += 1
            if i % 25 == 0 or i == len(todo):
                print(f"  [{i:4}/{len(todo)}] {r['date_sourced']}  {r['title'][:55]}")
        except Exception as e:
            errors += 1
            print(f"  [{i:4}/{len(todo)}] FAIL  {r['title'][:55]} — {str(e)[:80]}")
        time.sleep(0.25)

    print(f"\n=== {updated} marked Stale, {errors} errors ===")


if __name__ == "__main__":
    main()
