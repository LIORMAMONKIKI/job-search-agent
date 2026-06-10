"""Phase 3: Per-company deep dive for all companies in the DB.

For each company:
  - Pull metadata from Notion (Tier, Priority, Notes, Hiring Policy)
  - Claude + web_search call for: status / hiring policy / recent news / growth
  - Cross-ref warm_intros.csv → existing contacts at that company

Output:
  reports/companies/<slug>.json         one per company
  reports/companies_index_YYYY-MM-DD.json   summary index

Resume-safe: skips a company if its JSON already exists in the current run's
output (re-runs to retry the failures only).
"""
import csv
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

from anthropic import Anthropic
from notion_client import Client

from config import (
    NOTION_TOKEN,
    ANTHROPIC_API_KEY,
    COMPANIES_DATA_SOURCE_ID,
)
from notion_io import _extract_title, _extract_text, _extract_select


CSV_PATH = Path(__file__).parent / "warm_intros.csv"


def slugify(name):
    s = (name or "").lower().strip()
    s = re.sub(r"\s*\(.*?\)\s*", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unnamed"


def normalize_for_match(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def load_warm_intros():
    """{normalized_company_name: [intro dicts]}"""
    if not CSV_PATH.exists():
        return {}
    by_co = {}
    for r in csv.DictReader(open(CSV_PATH)):
        co = (r.get("Company_target") or "").strip()
        if not co:
            continue
        key = normalize_for_match(co)
        by_co.setdefault(key, []).append({
            "name": f"{(r.get('First Name') or '').strip()} {(r.get('Last Name') or '').strip()}".strip(),
            "position": (r.get("Position") or "").strip(),
            "linkedin": (r.get("URL") or "").strip(),
            "connected_on": (r.get("Connected On") or "").strip(),
        })
    return by_co


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
    out = []
    for row in rows:
        p = row["properties"]
        name = _extract_title(p.get("Company", {}))
        if not name:
            continue
        out.append({
            "id": row["id"],
            "name": name,
            "tier": _extract_select(p.get("Tier", {})),
            "priority": _extract_select(p.get("Priority", {})),
            "status": _extract_select(p.get("Status", {})),
            "hiring_policy": _extract_select(p.get("Hiring Policy", {})),
            "industry": _extract_select(p.get("Industry", {})),
            "size": _extract_select(p.get("Size", {})),
            "location": _extract_text(p.get("Location", {})),
            "notes": _extract_text(p.get("Notes", {})),
        })
    return out


# Cost model per call (Haiku 4.5, max_uses=1): ~8-10k input ($0.01) + ~700
# output ($0.0035) + $0.01 search fee ≈ $0.02-0.025. The 06-04 Sonnet run with
# unlimited searches averaged ~$0.25/call — search RESULTS billed as input
# were the dominant cost. See PLAYBOOK.md §1.
_IN_RATE, _OUT_RATE, _SEARCH_FEE = 1.0 / 1e6, 5.0 / 1e6, 0.01


def research_one(client, company, retries=2):
    """Claude + web_search call to research one company.

    Returns (research_dict, est_cost_usd)."""
    prompt = (
        f"Research the current ({date.today().isoformat()}) state of **{company['name']}**.\n\n"
        f"Context I already have: Tier {company.get('tier')}, "
        f"Priority {company.get('priority')}, Notes: {company.get('notes')[:200] if company.get('notes') else ''}\n\n"
        "Do ONE focused web search for fresh news (Apr–Jun 2026 preferred). Skip filler. "
        "Output ONLY a JSON object — no markdown, no code fences, no preamble — with this shape:\n"
        '{\n'
        '  "status_summary": "1 sentence: actively hiring / paused / layoffs / acquired / etc.",\n'
        '  "hiring_policy": "1-2 sentences: visa sponsorship, remote policy, EOR-friendly?",\n'
        '  "recent_news": ["bullet 1", "bullet 2", "bullet 3"],\n'
        '  "growth_signal": "1 sentence: headcount/funding/product trajectory",\n'
        '  "fit_for_lior": "1 sentence: how this company fits her Creative AI / Product Analyst / Vibe Coder threads",\n'
        '  "sources": ["url1", "url2"]\n'
        "}\n\n"
        "If no fresh sources found, fill with 'No recent signal' and leave sources empty."
    )
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1200,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}],
                messages=[{"role": "user", "content": prompt}],
            )
            u = getattr(resp, "usage", None)
            cost = _SEARCH_FEE
            if u is not None:
                cost += (getattr(u, "input_tokens", 0) or 0) * _IN_RATE
                cost += (getattr(u, "output_tokens", 0) or 0) * _OUT_RATE
            text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
            # Find the JSON object (last {...} block — model may have web_search reasoning text first)
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if not m:
                return {"error": f"no json: {text[:200]}"}, cost
            try:
                return json.loads(m.group(0)), cost
            except json.JSONDecodeError as e:
                return {"error": f"parse: {e}", "raw": m.group(0)[:300]}, cost
        except Exception as e:
            err = str(e)
            if ("429" in err or "rate_limit" in err) and attempt < retries - 1:
                wait = 65 * (attempt + 1)
                print(f"    rate-limited, waiting {wait}s …")
                time.sleep(wait)
                continue
            return {"error": err[:200]}, 0.0
    return {"error": "exhausted retries"}, 0.0


_PRIORITY_RANK = {"Top Target": 0, "High": 1, "Medium": 2, "Low": 3}


def run(only_priority=None, limit=None, budget=None, errored_only=False):
    """Run the full company research.

    only_priority: list like ['Top Target','High'] to filter — None = all.
    limit: cap for testing.
    budget: hard USD cap — stop cleanly when estimated spend reaches it.
    errored_only: only research companies whose existing report has an error
        (or no report) — for re-running a partially-failed batch.
    """
    if not NOTION_TOKEN or not ANTHROPIC_API_KEY:
        print("ERROR: missing keys")
        sys.exit(1)
    notion = Client(auth=NOTION_TOKEN)
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    print("Loading companies + warm intros...")
    companies = fetch_companies(notion)
    warm_by_co = load_warm_intros()
    if only_priority:
        companies = [c for c in companies if c.get("priority") in only_priority]

    out_dir_check = Path(__file__).parent / "reports" / "companies"
    if errored_only:
        def _needs_rerun(co):
            p = out_dir_check / f"{slugify(co['name'])}.json"
            if not p.exists():
                return True
            try:
                return "error" in (json.loads(p.read_text()).get("research") or {"error": 1})
            except Exception:
                return True
        companies = [c for c in companies if _needs_rerun(c)]

    # Highest-leverage first: priority rank, then warm-intro count desc.
    companies.sort(key=lambda c: (
        _PRIORITY_RANK.get(c.get("priority"), 9),
        -len(warm_by_co.get(normalize_for_match(c["name"]), [])),
    ))
    if limit:
        companies = companies[:limit]
    print(f"  {len(companies)} companies to research  ·  {sum(len(v) for v in warm_by_co.values())} warm intros indexed"
          + (f"  ·  budget ${budget:.2f}" if budget else ""))

    today = date.today().isoformat()
    out_dir = Path(__file__).parent / "reports" / "companies"
    out_dir.mkdir(parents=True, exist_ok=True)

    index = []
    spent = 0.0
    for i, co in enumerate(companies, 1):
        slug = slugify(co["name"])
        path = out_dir / f"{slug}.json"

        if budget and spent >= budget:
            print(f"  BUDGET REACHED (${spent:.2f} of ${budget:.2f}) — stopping cleanly at {i-1}/{len(companies)}")
            break

        # Resume — skip if this run already has a JSON for this company
        existing_today = False
        if path.exists():
            try:
                existing = json.loads(path.read_text())
                if existing.get("generated") == today and not existing.get("research", {}).get("error"):
                    existing_today = True
            except Exception:
                pass

        if existing_today:
            print(f"  [{i:3}/{len(companies)}] cached  {co['name']}")
            data = json.loads(path.read_text())
        else:
            print(f"  [{i:3}/{len(companies)}] research {co['name']}")
            research, cost = research_one(client, co)
            spent += cost
            intros = warm_by_co.get(normalize_for_match(co["name"]), [])
            data = {
                "generated": today,
                "company": co,
                "research": research,
                "warm_intros": intros,
                "intro_count": len(intros),
            }
            path.write_text(json.dumps(data, indent=2))
            time.sleep(15)  # pacing — Haiku w/ max_uses=1 is light on the search rate limit

        index.append({
            "slug": slug,
            "name": co["name"],
            "tier": co.get("tier"),
            "priority": co.get("priority"),
            "intro_count": data.get("intro_count", 0),
            "status_summary": data.get("research", {}).get("status_summary", "—"),
            "fit_for_lior": data.get("research", {}).get("fit_for_lior", "—"),
            "has_research": "error" not in data.get("research", {}),
        })

    index_path = Path(__file__).parent / "reports" / f"companies_index_{today}.json"
    index_path.write_text(json.dumps({"generated": today, "companies": index}, indent=2))
    print(f"Wrote {index_path}")
    print(f"Estimated spend this run: ${spent:.2f}")
    return str(index_path)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--priority", help="comma-separated: Top Target,High,Medium,Low")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--budget", type=float, default=None, help="hard USD cap; stops cleanly when reached")
    p.add_argument("--errored-only", action="store_true", help="only re-run companies whose report errored")
    args = p.parse_args()
    pri = [s.strip() for s in args.priority.split(",")] if args.priority else None
    run(only_priority=pri, limit=args.limit, budget=args.budget, errored_only=args.errored_only)
