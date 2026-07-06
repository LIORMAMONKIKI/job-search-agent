"""Hunter.io fallback tier — fill email gaps in the contact-channel map.

Post-processes reports/contact_channels.json: for companies whose best
channel is "LinkedIn TA search needed", query Hunter's domain-search for
email patterns + known addresses. Prioritizes Top Target / High companies
(re-queried from Notion) so the free tier's 25 searches/month go where
they matter.

Usage:
  python hunter_fill.py --cap 15     # default cap 15, leaves monthly headroom

Free tier budget: 25 domain searches / month. This script counts usage and
stops at the cap. $0 Anthropic spend.
"""
import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from notion_client import Client

from config import NOTION_TOKEN, COMPANIES_DATA_SOURCE_ID, HUNTER_API_KEY
from notion_io import _extract_title, _extract_select

MAP_PATH = Path(__file__).parent / "reports" / "contact_channels.json"
PRIORITY_RANK = {"Top Target": 0, "High": 1, "Medium": 2, "Low": 3}


def norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def get_domain(entry):
    for u in (entry.get("website"), entry.get("careers_url")):
        if not u:
            continue
        host = urlparse(u if u.startswith("http") else "https://" + u).netloc
        host = host.replace("www.", "")
        # careers subdomains → root domain
        parts = host.split(".")
        if len(parts) > 2 and parts[0] in ("careers", "jobs", "boards", "apply"):
            host = ".".join(parts[1:])
        if host:
            return host
    return None


def fetch_priorities():
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
    return {
        norm(_extract_title(row["properties"].get("Company", {}))):
            _extract_select(row["properties"].get("Priority", {})) or "Low"
        for row in rows
    }


def hunter_domain_search(domain):
    r = requests.get(
        "https://api.hunter.io/v2/domain-search",
        params={"domain": domain, "api_key": HUNTER_API_KEY, "limit": 5},
        timeout=15,
    )
    if r.status_code != 200:
        return None
    d = r.json().get("data") or {}
    emails = [e.get("value") for e in (d.get("emails") or []) if e.get("value")]
    return {"pattern": d.get("pattern"), "emails": emails[:5],
            "organization": d.get("organization")}


def run(cap=15):
    if not HUNTER_API_KEY:
        print("HUNTER_API_KEY missing in .env")
        return
    data = json.loads(MAP_PATH.read_text())
    companies = data["companies"]
    priorities = fetch_priorities()

    gaps = [c for c in companies
            if c.get("best_inquiry_channel") == "LinkedIn TA search needed"
            and get_domain(c)]
    gaps.sort(key=lambda c: PRIORITY_RANK.get(priorities.get(norm(c["company"]), "Low"), 3))

    print(f"{len(gaps)} gap companies · Hunter cap {cap}")
    used = 0
    for c in gaps:
        if used >= cap:
            break
        domain = get_domain(c)
        res = hunter_domain_search(domain)
        used += 1
        if res and (res["emails"] or res["pattern"]):
            c["hunter"] = res
            if res["emails"]:
                c["emails"] = res["emails"]
                c["best_inquiry_channel"] = res["emails"][0]
            elif res["pattern"]:
                guess = res["pattern"].replace("{first}", "FIRSTNAME").replace(
                    "{last}", "LASTNAME").replace("{f}", "F").replace("{l}", "L")
                c["best_inquiry_channel"] = f"pattern: {guess}@{domain}"
            pri = priorities.get(norm(c["company"]), "?")
            print(f"  ✓ {c['company'][:30]:30} ({pri:10}) → {c['best_inquiry_channel'][:55]}")
        else:
            print(f"  ✗ {c['company'][:30]:30} → hunter: nothing found ({domain})")
        time.sleep(0.6)

    MAP_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\nHunter searches used this run: {used} (free tier: 25/mo)")
    print(f"Updated {MAP_PATH}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=15)
    args = ap.parse_args()
    run(cap=args.cap)
