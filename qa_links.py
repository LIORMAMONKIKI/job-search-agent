"""QA every Careers URL in the Notion Companies DB.

For each company with a Careers URL set:
  - Structured ATS (Greenhouse/Lever/Ashby/Workable/SmartRecruiters):
    hit the JSON API → confirm valid response + count jobs
  - Custom/Unknown/Comeet: HTTP GET → check status + careers-page markers
  - Report: OK (with job count), WARN (page exists but empty), BROKEN (404/timeout/bad)

Output: qa_results_<date>.csv + qa_results_<date>.md (sorted: BROKEN first)
"""
import csv
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urlparse

import requests
from notion_client import Client

from config import NOTION_TOKEN, COMPANIES_DATA_SOURCE_ID
from notion_io import _extract_title, _extract_text, _extract_select


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


# Each entry: (label, function (url) → (status, n_jobs, note))
# status: "ok" | "warn" | "broken"
def slug_from_url(url, patterns):
    """Pull the company slug out of the careers URL."""
    for pattern in patterns:
        m = re.search(pattern, url, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def check_greenhouse(url):
    slug = slug_from_url(url, [
        r"greenhouse\.io/(?:boards/)?([a-z0-9_-]+)",
        r"greenhouse\.io/embed/job_board\?for=([a-z0-9_-]+)",
    ])
    if not slug:
        # URL doesn't match the canonical ATS-hosted pattern — fall back to HTML
        # check (e.g. Greenhouse board embedded into the company's own page).
        return check_html_page(url)
    api = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        r = requests.get(api, timeout=8, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            return "broken", 0, f"api {r.status_code}"
        data = r.json()
        n = len(data.get("jobs", []))
        if n == 0:
            return "warn", 0, "0 jobs"
        return "ok", n, ""
    except Exception as e:
        return "broken", 0, f"api err: {str(e)[:80]}"


def check_lever(url):
    slug = slug_from_url(url, [r"lever\.co/([a-z0-9_-]+)"])
    if not slug:
        return check_html_page(url)
    api = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = requests.get(api, timeout=8, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            return "broken", 0, f"api {r.status_code}"
        data = r.json()
        n = len(data) if isinstance(data, list) else 0
        if n == 0:
            return "warn", 0, "0 postings"
        return "ok", n, ""
    except Exception as e:
        return "broken", 0, f"api err: {str(e)[:80]}"


def check_ashby(url):
    slug = slug_from_url(url, [r"ashbyhq\.com/([a-z0-9_-]+)"])
    if not slug:
        return check_html_page(url)
    api = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        r = requests.get(api, timeout=8, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            return "broken", 0, f"api {r.status_code}"
        data = r.json()
        if isinstance(data, dict):
            n = len(data.get("jobs", data.get("postings", [])))
            if n == 0:
                return "warn", 0, "0 jobs"
            return "ok", n, ""
        return "warn", 0, "unexpected response shape"
    except Exception as e:
        return "broken", 0, f"api err: {str(e)[:80]}"


def check_workable(url):
    slug = slug_from_url(url, [r"workable\.com/([a-z0-9_-]+)"])
    if not slug:
        return check_html_page(url)
    api = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    try:
        r = requests.get(api, timeout=8, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            return "broken", 0, f"api {r.status_code}"
        data = r.json()
        n = len(data.get("jobs", []))
        if n == 0 and "name" in data:
            return "warn", 0, "account exists, 0 jobs"
        return ("ok" if n > 0 else "warn"), n, ""
    except Exception as e:
        return "broken", 0, f"api err: {str(e)[:80]}"


def check_smartrecruiters(url):
    slug = slug_from_url(url, [r"smartrecruiters\.com/([a-z0-9_-]+)"])
    if not slug:
        return check_html_page(url)
    api = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    try:
        r = requests.get(api, timeout=8, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            return "broken", 0, f"api {r.status_code}"
        data = r.json()
        n = len(data.get("content", []))
        if n == 0:
            return "warn", 0, "0 postings"
        return "ok", n, ""
    except Exception as e:
        return "broken", 0, f"api err: {str(e)[:80]}"


def check_html_page(url):
    """Generic HTML check — for Custom, Comeet, Unknown."""
    try:
        r = requests.get(
            url, timeout=10, allow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        )
    except Exception as e:
        return "broken", 0, f"http err: {str(e)[:80]}"
    if r.status_code != 200:
        return "broken", 0, f"http {r.status_code}"
    body = r.text[:80000].lower()
    markers = [
        "career", "jobs", "open positions", "open roles", "apply now",
        "we're hiring", "join our team", "join us",
        "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
        "comeet.co", "smartrecruiters.com", "myworkdayjobs.com",
    ]
    found = [m for m in markers if m in body]
    if not found:
        # Page is probably JS-rendered or wrong
        return "warn", 0, f"loaded but no career markers (body={len(r.text)} chars)"
    # Try to count job-like links on the page (proxy for job count)
    job_anchors = len(re.findall(r"<a[^>]+(?:job|position|role|career)[^>]*>", r.text, re.IGNORECASE))
    return "ok", job_anchors, f"markers={len(found)}"


ATS_CHECKERS = {
    "Greenhouse": check_greenhouse,
    "Lever": check_lever,
    "Ashby": check_ashby,
    "Workable": check_workable,
    "SmartRecruiters": check_smartrecruiters,
    "Comeet": check_html_page,
    "Teamtailor": check_html_page,
    "Workday": check_html_page,
    "BambooHR": check_html_page,
    "Custom": check_html_page,
    "Unknown": check_html_page,
}


def fetch_all_companies(notion):
    """Pull every company that has a Careers URL set."""
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

    companies = []
    for row in rows:
        props = row["properties"]
        name = _extract_title(props.get("Company", {}))
        if not name:
            continue
        url = props.get("Careers URL", {}).get("url")
        ats = _extract_select(props.get("ATS", {}))
        companies.append({
            "id": row["id"],
            "name": name,
            "tier": _extract_select(props.get("Tier", {})),
            "priority": _extract_select(props.get("Priority", {})),
            "careers_url": url,
            "ats": ats,
        })
    return companies


def qa_one(c):
    url = c.get("careers_url")
    ats = c.get("ats") or ""
    if not url:
        return {**c, "qa_status": "no_url", "qa_n_jobs": 0, "qa_note": ""}
    checker = ATS_CHECKERS.get(ats, check_html_page)
    status, n_jobs, note = checker(url)
    return {**c, "qa_status": status, "qa_n_jobs": n_jobs, "qa_note": note}


def main():
    notion = Client(auth=NOTION_TOKEN)
    print("Fetching all companies from Notion...")
    companies = fetch_all_companies(notion)
    with_url = [c for c in companies if c.get("careers_url")]
    print(f"  → {len(companies)} total, {len(with_url)} with Careers URL\n")

    print(f"Checking {len(with_url)} URLs concurrently...")
    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(qa_one, c): c for c in with_url}
        for i, f in enumerate(as_completed(futures), 1):
            r = f.result()
            results.append(r)
            icon = {"ok": "OK  ", "warn": "WARN", "broken": "FAIL"}.get(r["qa_status"], "?   ")
            print(f"  [{i:3}/{len(with_url)}] {icon} {r['name']:30} [{r['ats'] or '-':10}] {r['qa_status']:6} n_jobs={r['qa_n_jobs']:3}  {r['qa_note']}")

    # Sort: broken → warn → ok
    order = {"broken": 0, "warn": 1, "ok": 2}
    results.sort(key=lambda r: (order.get(r["qa_status"], 9), r["tier"] or "z", r["name"]))

    ok = sum(1 for r in results if r["qa_status"] == "ok")
    warn = sum(1 for r in results if r["qa_status"] == "warn")
    broken = sum(1 for r in results if r["qa_status"] == "broken")
    print(f"\n=== {ok} OK / {warn} warn / {broken} broken ===")

    today = date.today().isoformat()
    csv_path = f"qa_results_{today}.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name","tier","priority","ats","careers_url","qa_status","qa_n_jobs","qa_note","id"])
        w.writeheader()
        for r in results:
            w.writerow({
                "name": r["name"], "tier": r["tier"], "priority": r["priority"],
                "ats": r["ats"], "careers_url": r["careers_url"],
                "qa_status": r["qa_status"], "qa_n_jobs": r["qa_n_jobs"],
                "qa_note": r["qa_note"], "id": r["id"],
            })
    print(f"Wrote {csv_path}")

    # Markdown report
    md_path = f"qa_results_{today}.md"
    with open(md_path, "w") as f:
        f.write(f"# QA results — {today}\n\n")
        f.write(f"- OK: **{ok}**\n- Warn: **{warn}**\n- Broken: **{broken}**\n\n")
        f.write("| Status | Company | Tier | Priority | ATS | Jobs | URL | Note |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for r in results:
            icon = {"ok": "OK  ", "warn": "WARN", "broken": "FAIL"}.get(r["qa_status"], "?   ")
            f.write(f"| {icon} | {r['name']} | {r['tier'] or '—'} | {r['priority'] or '—'} | {r['ats'] or '—'} | {r['qa_n_jobs']} | {r['careers_url']} | {r['qa_note']} |\n")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
