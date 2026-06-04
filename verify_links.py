"""Verify every Sourced Role's JD Link is still alive.

For each role where the user hasn't acted (Action in New / Apply / Save / Interesting):
  - HEAD the JD Link with redirect-following
  - 404/410 → mark Stale (job removed)
  - LinkedIn returns 200 even for removed jobs and redirects to a generic page —
    detect by checking the final URL for /jobs/view/<digits>
  - 5xx / timeout / connection error → leave alone (transient)
  - 200 with valid URL → alive, no change

Runs as a stage in main.py (after Gmail, before judging) so dead listings get
purged from the active view on every weekly run.

Standalone:
  python verify_links.py [--limit N] [--dry-run]
"""
import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from notion_client import Client

from config import NOTION_TOKEN, SOURCED_ROLES_DATA_SOURCE_ID


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HTTP_TIMEOUT = 8

# Actions we'll check. Skip Applied / Dismiss / Stale (already triaged).
ACTIVE_ACTIONS = {"New", "Apply", "Save for Later", "Interesting but Not Now"}

_LINKEDIN_JOB_RE = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/\d+", re.IGNORECASE)
_LINKEDIN_HOST_RE = re.compile(r"linkedin\.com", re.IGNORECASE)


def classify(url):
    """Returns one of 'alive', 'dead', 'unknown'."""
    if not url:
        return "unknown"
    try:
        # HEAD first; some servers don't like it, fall back to GET
        r = requests.head(
            url,
            allow_redirects=True,
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        )
        if r.status_code == 405 or r.status_code >= 500:
            # Some sites disallow HEAD or transient 5xx — try GET
            r = requests.get(
                url,
                allow_redirects=True,
                timeout=HTTP_TIMEOUT,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                stream=True,
            )
            r.close()
    except requests.exceptions.Timeout:
        return "unknown"
    except requests.exceptions.RequestException:
        return "unknown"

    code = r.status_code
    final = r.url or url

    # Hard dead
    if code in (404, 410):
        return "dead"
    # Other 4xx — probably permission/auth issues, leave alone
    if 400 <= code < 500:
        return "unknown"
    # Transient
    if code >= 500:
        return "unknown"

    # LinkedIn special-case: removed jobs redirect to a generic page that's still 200.
    # The canonical job URL is linkedin.com/(comm/)?jobs/view/<digits>.
    if _LINKEDIN_HOST_RE.search(final) and not _LINKEDIN_JOB_RE.search(final):
        return "dead"

    return "alive"


def fetch_active_roles(notion):
    """All roles with Action in ACTIVE_ACTIONS and a non-empty JD Link."""
    rows, cursor = [], None
    or_filters = [{"property": "Action", "select": {"equals": a}} for a in ACTIVE_ACTIONS]
    while True:
        kw = {
            "data_source_id": SOURCED_ROLES_DATA_SOURCE_ID,
            "page_size": 100,
            "filter": {
                "and": [
                    {"or": or_filters},
                    {"property": "JD Link", "url": {"is_not_empty": True}},
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
        url = (p.get("JD Link", {}) or {}).get("url")
        if not url:
            continue
        title_items = p.get("Role Title", {}).get("title", [])
        title = "".join(i.get("plain_text", "") for i in title_items) or "(no title)"
        out.append({"id": row["id"], "title": title, "url": url})
    return out


def mark_stale(notion, page_id):
    notion.pages.update(
        page_id=page_id,
        properties={"Action": {"select": {"name": "Stale"}}},
    )


def verify_links(notion=None, verbose=True, limit=None, dry_run=False, max_workers=12):
    """Main entry point. Can be called from main.py."""
    if notion is None:
        notion = Client(auth=NOTION_TOKEN)

    if verbose:
        print("  Fetching active roles from Notion...")
    todo = fetch_active_roles(notion)
    if limit:
        todo = todo[:limit]
    if verbose:
        print(f"  Checking {len(todo)} active roles' JD Links")

    alive, dead, unknown = 0, 0, 0
    dead_rows = []

    # Concurrent HEAD probes
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(classify, r["url"]): r for r in todo}
        for i, f in enumerate(as_completed(futures), 1):
            r = futures[f]
            verdict = f.result()
            if verdict == "alive":
                alive += 1
            elif verdict == "dead":
                dead += 1
                dead_rows.append(r)
            else:
                unknown += 1
            if verbose and i % 50 == 0:
                print(f"    progress {i}/{len(todo)}  alive={alive} dead={dead} unknown={unknown}")

    if verbose:
        print(f"  → {alive} alive · {dead} dead · {unknown} transient/unknown")

    # Mark dead ones as Stale
    marked, errors = 0, 0
    if dead_rows and not dry_run:
        if verbose:
            print(f"  Marking {len(dead_rows)} dead roles as Stale...")
        for r in dead_rows:
            try:
                mark_stale(notion, r["id"])
                marked += 1
            except Exception as e:
                errors += 1
                if verbose:
                    print(f"    FAIL {r['title'][:55]} — {str(e)[:60]}")
            time.sleep(0.2)  # gentle Notion rate limit

    if verbose:
        print(f"  → {marked} marked Stale, {errors} write errors")
    return {"alive": alive, "dead": dead, "unknown": unknown, "marked": marked, "errors": errors}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not NOTION_TOKEN:
        print("ERROR: NOTION_TOKEN missing")
        sys.exit(1)
    verify_links(verbose=True, limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
