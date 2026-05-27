"""Step 1 smoke test — all 13 keywords, LinkedIn only, no date filter.

Prints summary counts + a sample of titles per keyword so we can eyeball
what's coming through.
"""
import pandas as pd
from collections import Counter
from scraper import search_jobs_by_keyword
from config import SEARCH_KEYWORDS, SCRAPER_SITES, SCRAPER_COUNTRY

print(f"Config: sites={SCRAPER_SITES}, country={SCRAPER_COUNTRY}, no date filter")
print(f"Keywords ({len(SEARCH_KEYWORDS)}): {SEARCH_KEYWORDS}")
print("=" * 80)

all_rows = []

for kw in SEARCH_KEYWORDS:
    print(f"\n→ '{kw}'")
    rows = search_jobs_by_keyword(kw, results_wanted=25)
    print(f"  {len(rows)} rows")
    for r in rows:
        r["_keyword"] = kw
    all_rows.extend(rows)
    # show top 5 titles
    for r in rows[:5]:
        title = (r.get("title") or "?")[:55]
        company = (r.get("company") or "?")[:20]
        loc = str(r.get("location") or "?")[:20]
        print(f"    {title:55} | {company:20} | {loc}")

print("\n" + "=" * 80)
print(f"TOTAL raw rows: {len(all_rows)}")

if all_rows:
    df = pd.DataFrame(all_rows).drop_duplicates(subset=["job_url"])
    print(f"After dedupe by job_url: {len(df)}")
    print(f"\nTop companies surfaced:")
    for company, n in Counter(df["company"].fillna("?")).most_common(15):
        print(f"  {n:3} × {company}")
