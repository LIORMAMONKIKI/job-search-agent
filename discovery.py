"""Phase 4: Discover new growth-stage companies worth watching.

Surfaces companies NOT already in the Companies DB that look relevant to Lior's
skill thread (AI Creative / Creative Tech / Vibe Coding / Agents). Israel + US.

Output: reports/discovery_YYYY-MM-DD.md
"""
import json
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
from notion_io import _extract_title


SEARCH_PROMPTS = [
    # (region, prompt)
    ("Israel", "AI / Creative AI / Creative Tech startups in Israel raised in 2026 or growing aggressively, "
               "especially generative image/video, AI video tools, AI creative production, agentic tooling"),
    ("Israel", "Israeli AI agents / AI automation startups hiring in 2026 — LangChain/LangGraph ecosystem, "
               "AI-pair-programming, vibe coding adjacent"),
    ("United States", "US AI Creative / Creative Tech startups in growth stage 2026 — Generative video, image, "
                       "audio, brand AI, AI marketing creative"),
    ("United States", "US AI agents and automation startups hiring in 2026 — LangChain, MCP, vibe coding, "
                       "AI-augmented IDE, autonomous agents"),
    ("Israel", "Israeli startups recently funded that build with open models / Llama / Mistral / Qwen, "
               "or sell open-model infrastructure"),
    ("United States", "US open-source AI / open weights ecosystem companies hiring in 2026"),
]


def fetch_known_company_names(notion):
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
    names = set()
    for row in rows:
        name = _extract_title(row["properties"].get("Company", {}))
        if name:
            names.add(re.sub(r"[^a-z0-9]", "", name.lower()))
    return names


def search_one(client, region, prompt):
    """Returns a markdown bulleted list of discovered companies."""
    full = (
        f"Use web search to find companies matching this brief:\n\n"
        f"REGION: {region}\nFOCUS: {prompt}\n\n"
        f"Filter for companies that are:\n"
        f"  - Currently active (not shut down) and growth-stage (Series A–C, or pre-IPO unicorns)\n"
        f"  - Plausibly hiring for creative-AI / creative-tech / vibe-coding / agentic roles\n"
        f"  - Likely to interest a Tel Aviv-based AI Creative Specialist with analyst chops "
        f"and a vibe-coding fluency (Lior — built a Sliding Doors LoRA, ex-Artlist, hunting Creative Technologist / "
        f"AI Solutions Engineer / Product Analyst at creative-AI cos)\n\n"
        f"Output ONLY a JSON array, no preamble, no fences. Each item:\n"
        f'  {{"name": "...", "what_they_do": "1 sentence", "why_relevant": "1 sentence — tie to Lior\'s threads", '
        f'"signal": "funding/launch/news with date", "url": "homepage or careers"}}\n\n'
        f"Aim for 5-8 strong candidates. Skip filler. If nothing fresh, output []."
    )
    for attempt in range(2):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1500,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": full}],
            )
            text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
            m = re.search(r"\[.*\]", text, re.DOTALL)
            if not m:
                return []
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return []
        except Exception as e:
            err = str(e)
            if ("429" in err or "rate_limit" in err) and attempt == 0:
                time.sleep(65)
                continue
            return []
    return []


def run():
    if not NOTION_TOKEN or not ANTHROPIC_API_KEY:
        print("ERROR: missing keys")
        sys.exit(1)
    notion = Client(auth=NOTION_TOKEN)
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    print("Loading existing company names for dedup...")
    known = fetch_known_company_names(notion)
    print(f"  {len(known)} companies already in DB")

    found_by_region = {"Israel": [], "United States": []}
    seen = set()
    for i, (region, prompt) in enumerate(SEARCH_PROMPTS, 1):
        print(f"[{i}/{len(SEARCH_PROMPTS)}] {region}: {prompt[:70]}...")
        candidates = search_one(client, region, prompt)
        for c in candidates:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            key = re.sub(r"[^a-z0-9]", "", name.lower())
            if key in known:
                continue  # already in our DB — not a discovery
            if key in seen:
                continue
            seen.add(key)
            found_by_region[region].append(c)
        time.sleep(35)

    today = date.today().isoformat()
    out_dir = Path(__file__).parent / "reports"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"discovery_{today}.md"

    md = [
        f"# New companies to watch ({today})\n",
        "Surfaced via web search. Filtered against your existing Companies DB — "
        "these are NOT already tracked. Cross-check before adding.\n",
    ]
    for region in ("Israel", "United States"):
        items = found_by_region[region]
        md.append(f"\n## {region} ({len(items)})\n")
        if not items:
            md.append("\n_No new candidates surfaced this run._\n")
            continue
        for c in items:
            url = c.get("url") or ""
            md.append(
                f"\n### {c.get('name')}\n"
                f"- **What:** {c.get('what_they_do', '—')}\n"
                f"- **Why relevant:** {c.get('why_relevant', '—')}\n"
                f"- **Signal:** {c.get('signal', '—')}\n"
                + (f"- **URL:** [{url}]({url})\n" if url else "")
            )

    path.write_text("\n".join(md))
    print(f"Wrote {path}")
    json_path = out_dir / f"discovery_{today}.json"
    json_path.write_text(json.dumps({"generated": today, "by_region": found_by_region}, indent=2))
    return str(path)


if __name__ == "__main__":
    run()
