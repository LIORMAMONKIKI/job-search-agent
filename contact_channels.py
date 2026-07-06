"""Bulk contact-channel mapper — the inquiry infrastructure.

For every company in the Companies DB, find the reachable inquiry channels:
  - emails on their site (careers@ / jobs@ / hr@ / hello@ / info@ ...)
  - contact page URL
  - whether we already hold warm TA/recruiting contacts (warm_intros.csv)
  - their ATS (already enriched) → application-form channel

$0 — pure HTTP scraping (requests + regex), no model calls, no paid APIs.
Polite: 2 pages/site max, 8s timeout, 0.7s delay between requests.

Output:
  reports/contact_channels.json   full map
  stdout                          coverage summary

Usage:
  python contact_channels.py             # all companies
  python contact_channels.py --limit 20  # test run
"""
import argparse
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from notion_client import Client

from config import NOTION_TOKEN, COMPANIES_DATA_SOURCE_ID
from notion_io import _extract_title, _extract_text, _extract_select

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# emails we don't care about
JUNK = ("example.", "sentry", "wixpress", "@2x", ".png", ".jpg", ".gif",
        "noreply", "no-reply", "donotreply", "privacy@", "legal@", "dpo@",
        "abuse@", "security@", "support@")
# priority order for inquiry purposes
PRIORITY = ("careers", "jobs", "talent", "recruit", "hr@", "people",
            "hello", "contact", "info", "team", "press")

CONTACT_PATHS = ("/contact", "/contact-us", "/about", "/careers", "/jobs")


def fetch(url, timeout=8):
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            return r.text
    except Exception:
        pass
    return ""


def extract_emails(html):
    found = set()
    for e in EMAIL_RE.findall(html or ""):
        el = e.lower().strip(".")
        if any(j in el for j in JUNK):
            continue
        if el.endswith((".png", ".jpg", ".svg", ".webp", ".css", ".js")):
            continue
        found.add(el)
    return sorted(found)


def rank_email(e):
    for i, p in enumerate(PRIORITY):
        if p in e:
            return i
    return len(PRIORITY)


def load_warm_ta():
    """Companies where Lior already knows TA/recruiting people."""
    path = Path(__file__).parent / "warm_intros.csv"
    if not path.exists():
        return {}
    import csv
    ta_by_co = {}
    for r in csv.DictReader(open(path)):
        pos = (r.get("Position") or "").lower()
        co = (r.get("Company_target") or "").strip()
        if not co:
            continue
        if any(k in pos for k in ("talent", "recruit", "hr ", "people")):
            name = f"{r.get('First Name','')} {r.get('Last Name','')}".strip()
            ta_by_co.setdefault(re.sub(r"[^a-z0-9]", "", co.lower()), []).append(
                {"name": name, "position": r.get("Position", ""),
                 "linkedin": r.get("URL", "")})
    return ta_by_co


def run(limit=None):
    notion = Client(auth=NOTION_TOKEN)
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

    ta_map = load_warm_ta()
    out, n_email, n_ta, n_ats = [], 0, 0, 0
    if limit:
        rows = rows[:limit]

    for i, row in enumerate(rows, 1):
        p = row["properties"]
        name = _extract_title(p.get("Company", {}))
        if not name:
            continue
        website = (p.get("Website", {}) or {}).get("url") or ""
        careers = (p.get("Careers URL", {}) or {}).get("url") or ""
        ats = _extract_select(p.get("ATS", {})) or ""

        emails = set()
        pages_tried = []
        if website:
            base = website if website.startswith("http") else "https://" + website
            html = fetch(base)
            pages_tried.append(base)
            emails.update(extract_emails(html))
            # one contact-ish page
            for path in CONTACT_PATHS:
                if len(pages_tried) >= 2:
                    break
                u = urljoin(base, path)
                h = fetch(u)
                if h:
                    pages_tried.append(u)
                    emails.update(extract_emails(h))
            time.sleep(0.7)
        if careers and len(pages_tried) < 3:
            h = fetch(careers)
            if h:
                emails.update(extract_emails(h))
            time.sleep(0.7)

        ranked = sorted(emails, key=rank_email)
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        ta = ta_map.get(key, [])

        best_channel = (
            f"warm TA: {ta[0]['name']}" if ta
            else ranked[0] if ranked
            else f"application form ({ats})" if ats and ats.lower() != "custom"
            else "LinkedIn TA search needed"
        )
        entry = {
            "company": name,
            "website": website,
            "careers_url": careers,
            "ats": ats,
            "emails": ranked[:5],
            "warm_ta_contacts": ta,
            "best_inquiry_channel": best_channel,
        }
        out.append(entry)
        n_email += bool(ranked)
        n_ta += bool(ta)
        n_ats += bool(ats and ats.lower() != "custom")
        print(f"[{i:3}/{len(rows)}] {name[:32]:32} → {best_channel[:60]}")

    today = date.today().isoformat()
    out_path = Path(__file__).parent / "reports" / "contact_channels.json"
    out_path.write_text(json.dumps(
        {"generated": today, "companies": out}, indent=2, ensure_ascii=False))

    print(f"\n=== COVERAGE ===")
    print(f"companies scanned : {len(out)}")
    print(f"email found       : {n_email}")
    print(f"warm TA contact   : {n_ta}")
    print(f"ATS form channel  : {n_ats}")
    no_channel = sum(1 for e in out
                     if e['best_inquiry_channel'] == 'LinkedIn TA search needed')
    print(f"needs TA search   : {no_channel}")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
