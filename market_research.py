"""Phase 2: Hiring-trends market research.

Uses Claude + web_search tool for fresh data. Topics × regions:
  AI · Creative AI · Creative Tech · Open Models · Vibe Coding · Agents/Automation
  × {Israel, US}

Pacing handles Anthropic's 30k tokens/min rate limit. Skips a topic
gracefully on errors so a single failure doesn't kill the whole report.

Output: reports/market_YYYY-MM-DD.md
"""
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

from anthropic import Anthropic

from config import ANTHROPIC_API_KEY


TOPICS = [
    "AI roles and hiring",
    "Creative AI / generative video / image hiring",
    "Creative Technologist / Technical Artist hiring",
    "Open-weight models ecosystem (Llama, Mistral, Qwen) hiring",
    "Vibe coding / AI-pair-programming / agentic IDE hiring",
    "AI agents and automation (LangChain, LangGraph, Make, n8n) hiring",
]
REGIONS = ["Israel", "United States"]


def research_one(client, topic, region, retries=2):
    """One Claude + web_search call per (topic, region) pair."""
    prompt = (
        f"Research the **current ({date.today().isoformat()})** hiring landscape for "
        f"**{topic}** in **{region}**. Use web search for fresh, reliable sources "
        "(industry reports, company blogs, recruiter reports, news from Apr–Jun 2026). "
        "Skip anything older than 6 months unless it's seminal context.\n\n"
        "Produce a tight markdown section with subsections:\n"
        "  - **What's growing / shrinking** (with 1-2 source links inline)\n"
        "  - **New role titles emerging**\n"
        "  - **Hottest companies hiring aggressively right now**\n"
        "  - **Stack / tools in demand**\n"
        f"  - **{region}-specific dynamics** (visa, ecosystem, compensation if surfaced)\n\n"
        "Concrete > generic. Skip if you can't find good fresh sources — just say "
        "'No recent signal' in that subsection. No preamble."
    )
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1800,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": prompt}],
            )
            # Concatenate text blocks (web_search interleaves tool_use/text)
            chunks = [b.text for b in resp.content if hasattr(b, "text")]
            return "\n\n".join(chunks).strip()
        except Exception as e:
            err = str(e)
            if ("429" in err or "rate_limit" in err) and attempt < retries - 1:
                wait = 65 * (attempt + 1)
                print(f"    rate-limited, waiting {wait}s before retry {attempt+1}/{retries}")
                time.sleep(wait)
                continue
            return f"_Skipped ({topic} / {region}) — error: {err[:120]}_"
    return f"_Skipped ({topic} / {region}) — exhausted retries_"


def run():
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY missing")
        sys.exit(1)
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    sections = []
    total = len(TOPICS) * len(REGIONS)
    i = 0
    for region in REGIONS:
        sections.append(f"\n## {region}\n")
        for topic in TOPICS:
            i += 1
            print(f"[{i}/{total}] {region} — {topic}")
            section = research_one(client, topic, region)
            sections.append(f"\n### {topic}\n\n{section}\n")
            # Pace to stay under 30k input tokens/min — web_search returns are heavy
            time.sleep(35)

    today = date.today().isoformat()
    reports_dir = Path(__file__).parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    path = reports_dir / f"market_{today}.md"
    md = (
        f"# Market Research — Hiring Trends ({today})\n\n"
        "Up-to-date as of generation time. Sourced via Claude + web search. "
        "Cross-check before relying on specific facts.\n"
        + "".join(sections)
    )
    path.write_text(md)
    print(f"Wrote {path}")
    return str(path)


if __name__ == "__main__":
    run()
