"""Read enrichment CSV and write Careers URL + ATS back to Notion."""
import csv
import sys
import time
from datetime import date
from notion_client import Client
from config import NOTION_TOKEN


def main(csv_path):
    notion = Client(auth=NOTION_TOKEN)
    rows = list(csv.DictReader(open(csv_path)))

    updates = [r for r in rows if r.get("careers_url") and r.get("page_id")]
    print(f"Writing {len(updates)} updates to Notion...")

    today = date.today().isoformat()
    ok, fail = 0, 0
    for i, r in enumerate(updates, 1):
        props = {
            "Careers URL": {"url": r["careers_url"]},
            "Last Checked": {"date": {"start": today}},
        }
        if r.get("ats"):
            props["ATS"] = {"select": {"name": r["ats"]}}

        try:
            notion.pages.update(page_id=r["page_id"], properties=props)
            ok += 1
            print(f"  [{i:3}/{len(updates)}] OK   {r['company']:25} [{r.get('ats','-'):16}] {r['careers_url']}")
        except Exception as e:
            fail += 1
            print(f"  [{i:3}/{len(updates)}] FAIL {r['company']:25} ERROR: {str(e)[:100]}")
        time.sleep(0.3)  # gentle Notion rate-limit

    print(f"\n=== {ok} written, {fail} errors ===")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "enrichment_T3_2026-05-20.csv"
    main(path)
