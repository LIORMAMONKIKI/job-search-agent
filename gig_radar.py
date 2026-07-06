"""Gig Radar — automated sweep of public gig/job boards for lane-fit work.

Watches sources that expose public APIs/feeds (no login, no scraping fights):
  - RemoteOK (JSON API)
  - We Work Remotely (RSS)
  - ai-jobs.net (RSS)
  - Himalayas (RSS)
  - Mercor public listings (HTML, best-effort)
  - Twine creative gigs (HTML, best-effort)

Reuses the lane scorer from daily_brief.py. $0 — no model calls.
Signup-gated marketplaces (Outlier, DataAnnotation, Alignerr, Surge...)
can't be watched from outside — their notification emails flow into the
daily brief once Lior signs up (senders already configured there).

Output: reports/gig_radar_YYYY-MM-DD.md (+ stdout)
"""
import json
import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import requests

from daily_brief import score_line, classify_location

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
TIMEOUT = 12


def _get(url, as_json=False):
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json() if as_json else r.text
    except Exception:
        pass
    return None


def src_remoteok():
    """RemoteOK public JSON API."""
    data = _get("https://remoteok.com/api", as_json=True)
    out = []
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict) or not item.get("position"):
            continue
        line = f"{item.get('position','')} at {item.get('company','')} ({item.get('location','remote')})"
        out.append((line, item.get("url", "")))
    return out


def _rss_items(url):
    text = _get(url)
    if not text:
        return []
    try:
        root = ET.fromstring(text.encode() if isinstance(text, str) else text)
    except Exception:
        return []
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if title:
            items.append((title, link))
    return items


def src_wwr():
    return _rss_items("https://weworkremotely.com/categories/remote-design-jobs.rss") + \
           _rss_items("https://weworkremotely.com/remote-jobs.rss")


def src_aijobs():
    return _rss_items("https://ai-jobs.net/feed/")


def src_himalayas():
    return _rss_items("https://himalayas.app/jobs/rss")


def src_mercor():
    """Best-effort: Mercor public listings page."""
    html = _get("https://work.mercor.com/") or _get("https://www.mercor.com/jobs") or ""
    out = []
    # crude title extraction from listing markup
    for m in re.finditer(r'>([^<>]{10,90}?(?:Expert|Specialist|Trainer|Annotator|Evaluator|Creative|Video|Visual|Design)[^<>]{0,60})<', html):
        t = m.group(1).strip()
        if t and not t.lower().startswith(("we ", "the ", "our ")):
            out.append((f"{t} (Mercor)", "https://work.mercor.com/"))
    return list(dict.fromkeys(out))[:15]


def src_twine():
    html = _get("https://www.twine.net/jobs") or ""
    out = []
    for m in re.finditer(r'>([^<>]{10,90}?(?:AI|Video|Motion|Animator|Creative|Editor)[^<>]{0,50})<', html):
        t = m.group(1).strip()
        out.append((f"{t} (Twine)", "https://www.twine.net/jobs"))
    return list(dict.fromkeys(out))[:10]


SOURCES = [
    ("RemoteOK", src_remoteok),
    ("WeWorkRemotely", src_wwr),
    ("ai-jobs.net", src_aijobs),
    ("Himalayas", src_himalayas),
    ("Mercor", src_mercor),
    ("Twine", src_twine),
]


def run():
    hits, seen = [], set()
    stats = {}
    for name, fn in SOURCES:
        try:
            items = fn()
        except Exception:
            items = []
        stats[name] = len(items)
        for line, url in items:
            key = re.sub(r"[^a-z0-9]", "", line.lower())[:70]
            if key in seen:
                continue
            seen.add(key)
            s = score_line(line)
            if s >= 2:
                hits.append({"line": line, "url": url, "score": s, "src": name})

    hits.sort(key=lambda h: -h["score"])
    today = date.today().isoformat()
    md = [f"# Gig Radar — {today}", ""]
    scanned = sum(stats.values())
    md.append(f"_Scanned {scanned} listings across {len(SOURCES)} public boards "
              f"({', '.join(f'{k}:{v}' for k, v in stats.items())})_")
    md.append("")
    if hits:
        md.append("## 🎯 Lane-fit gigs & roles")
        for h in hits[:15]:
            md.append(f"- **{h['line']}** — [{h['src']}]({h['url']}) _(score {h['score']})_")
    else:
        md.append("_No lane-fit listings today across the public boards._")
    out = Path(__file__).parent / "reports" / f"gig_radar_{today}.md"
    out.write_text("\n".join(md))
    print("\n".join(md))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    run()
