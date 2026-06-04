"""Phase 1: Internal analysis of Sourced Roles.

Aggregates the DB into:
  - Top titles (frequency)
  - Top matched skills + platforms
  - Skill gaps weighted by demand (must appear in ≥2 roles)
  - Build-from tag distribution
  - Israel vs International bucket comparison

Calls Claude once per bucket to synthesize key insights / trends. Cheap (~$0.50–1).

Output:
  reports/insights_YYYY-MM-DD.json
  reports/insights_YYYY-MM-DD.md
"""
import json
import os
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from anthropic import Anthropic
from notion_client import Client

from config import (
    NOTION_TOKEN,
    ANTHROPIC_API_KEY,
    SOURCED_ROLES_DATA_SOURCE_ID,
)
from notion_io import _extract_title, _extract_text, _extract_select


_ISRAEL_RE = re.compile(
    r"\b(tel aviv|israel|jerusalem|ramat gan|herzliya|haifa|yokne'?am|netanya|rishon|holon|petah|hod hasharon)\b",
    re.IGNORECASE,
)


# Match Lior's skill name normalisation to SKILLS.md spelling
_SKILL_ALIASES = {
    "comfy": "ComfyUI", "comfyui": "ComfyUI",
    "lora": "LoRA training", "lora training": "LoRA training",
    "lora fine-tuning": "LoRA training", "lora fine tuning": "LoRA training",
    "flux": "Flux", "fal": "Fal", "ltx": "LTX",
    "kling": "Kling", "seedance": "Seedance",
    "midjourney": "Midjourney", "runway": "Runway", "pika": "Pika",
    "11labs": "ElevenLabs", "elevenlabs": "ElevenLabs",
    "claude code": "Claude Code", "cursor": "Cursor",
    "lovable": "Lovable", "base44": "Base44", "wix studio": "Wix Studio",
    "figma": "Figma",
    "python": "Python", "sql": "SQL", "tableau": "Tableau",
    "mixpanel": "Mixpanel", "a/b testing": "A/B testing",
    "premiere": "Adobe Premiere", "after effects": "After Effects", "ae": "After Effects",
    "motion graphics": "Motion graphics",
    "langchain": "LangChain", "langgraph": "LangGraph",
    "prompt engineering": "Prompt engineering",
    "agent": "Agent workflows", "agents": "Agent workflows", "agent workflows": "Agent workflows",
}


def bucket_for_location(loc):
    if not isinstance(loc, str) or not loc.strip():
        return "unknown"
    return "israel" if _ISRAEL_RE.search(loc) else "international"


def fetch_all_roles(notion):
    """All Sourced Roles, with the fields needed for analysis."""
    rows, cursor = [], None
    while True:
        kw = {"data_source_id": SOURCED_ROLES_DATA_SOURCE_ID, "page_size": 100}
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
        title_items = p.get("Role Title", {}).get("title", [])
        title = "".join(i.get("plain_text", "") for i in title_items) or ""
        out.append({
            "id": row["id"],
            "title": title.strip(),
            "location": _extract_text(p.get("Location", {})),
            "skill_match": _extract_select(p.get("Skill-Thread Match", {})),
            "action": _extract_select(p.get("Action", {})),
            "visa": _extract_select(p.get("Visa Likelihood", {})),
            "build_from": [t["name"] for t in p.get("Build-From Tags", {}).get("multi_select", [])],
            "matched_skills": [t["name"] for t in p.get("Matched Skills", {}).get("multi_select", [])],
            "gap_skills": [t["name"] for t in p.get("Gap Skills", {}).get("multi_select", [])],
            "why": _extract_text(p.get("Why Surfaced", {})),
            "pitch": _extract_text(p.get("Pitch Angle", {})),
        })
    return out


def normalize_skill(s):
    """Map a freeform skill string to its canonical name in SKILLS.md."""
    if not s:
        return None
    k = s.strip().lower()
    return _SKILL_ALIASES.get(k, s.strip())


def normalize_title(t):
    """Collapse minor variations in role titles for frequency counting."""
    if not t:
        return None
    s = t.strip()
    # Strip seniority modifiers + parens at end
    s = re.sub(r"\s*\(.*?\)\s*$", "", s)
    s = re.sub(r"^(senior|sr\.?|jr\.?|junior|lead|principal|staff|head of)\s+", "", s, flags=re.I)
    return s


def aggregate(roles, label):
    """Compute frequency tables for one bucket."""
    titles = Counter()
    matched = Counter()
    gaps = Counter()
    build = Counter()
    matches = Counter()

    for r in roles:
        # Skip the auto-archived ones for "what's the market asking" stats
        if r["action"] in ("Stale", "Dismiss"):
            continue
        t = normalize_title(r["title"])
        if t:
            titles[t] += 1
        for s in r["matched_skills"]:
            ns = normalize_skill(s)
            if ns:
                matched[ns] += 1
        for s in r["gap_skills"]:
            ns = normalize_skill(s)
            if ns:
                gaps[ns] += 1
        for b in r["build_from"]:
            build[b] += 1
        if r["skill_match"]:
            matches[r["skill_match"]] += 1

    return {
        "label": label,
        "n_roles": len([r for r in roles if r["action"] not in ("Stale", "Dismiss")]),
        "n_total_including_archived": len(roles),
        "top_titles": titles.most_common(25),
        "top_matched_skills": matched.most_common(40),
        "top_gap_skills": gaps.most_common(20),
        "build_from_tags": build.most_common(),
        "skill_match_distribution": matches.most_common(),
    }


def gap_by_demand(agg, min_count=2):
    """Skills appearing in ≥ min_count roles where Lior is Gap/Familiar/Learning.

    Reads SKILLS.md. Only uses gap_skills as the demand signal (skills the
    judge specifically flagged as gaps for Lior on a particular role) — NOT
    matched_skills. Otherwise skills she's strong at get counted as "demanded"
    and matched against SKILLS.md as if they were gaps, which produces false
    positives (e.g. Python in matched_skills but SKILLS.md lists it as
    "Python (Pandas, NumPy, Scikit-learn)" — fuzzy match fails → false gap).
    """
    skills_path = Path(__file__).parent / "SKILLS.md"
    skills_text = skills_path.read_text() if skills_path.exists() else ""

    # Parse SKILLS.md table lines like: | Skill name | Level | ...
    lior_level = {}
    for line in skills_text.splitlines():
        m = re.match(r"\|\s*([^|]+?)\s*\|\s*(Expert|Proficient|Familiar|Learning|Gap)[^|]*\|", line)
        if m:
            name = m.group(1).strip()
            lvl = m.group(2).strip()
            # Also index by the first token (e.g. "Python (Pandas, NumPy ...)" → "python")
            lior_level[name.lower()] = lvl
            first_tok = re.split(r"\s*\(", name)[0].lower().strip()
            if first_tok and first_tok not in lior_level:
                lior_level[first_tok] = lvl

    # The "demand" for gap analysis comes ONLY from gap_skills (judge's verdict),
    # not matched_skills. That's the whole point of the field.
    demand = Counter()
    for name, n in agg["top_gap_skills"]:
        demand[name] += n

    real_gaps = []
    for name, n in demand.most_common():
        if n < min_count:
            break
        lvl = lior_level.get(name.lower())
        if lvl in ("Familiar", "Learning", "Gap"):
            real_gaps.append({"skill": name, "demand_count": n, "lior_level": lvl})
        elif lvl is None:
            real_gaps.append({"skill": name, "demand_count": n, "lior_level": "Not listed"})
        # Skip Expert/Proficient — judge made a mistake on those rows, don't aggregate them
    return real_gaps[:15]


def synthesize_insights(client, agg, gaps, label):
    """One Claude call per bucket to produce the human-readable insight section."""
    system = (
        "You are analyzing job-market data for Lior, who is hunting for AI Creative / "
        "Creative Technologist / Product Analyst / Vibe Coder roles. Be sharp, concrete, "
        "no fluff. Tie observations to actionable patterns. Use bullet points."
    )
    payload = {
        "bucket": label,
        "role_count": agg["n_roles"],
        "top_titles": agg["top_titles"][:15],
        "top_skills": agg["top_matched_skills"][:25],
        "build_from_distribution": agg["build_from_tags"],
        "skill_match_distribution": agg["skill_match_distribution"],
        "demand_weighted_gaps": gaps,
    }
    prompt = (
        f"DATA for the **{label}** bucket:\n\n```json\n{json.dumps(payload, indent=2)}\n```\n\n"
        "Produce a markdown section with these subsections:\n"
        "  ### Key job titles\n"
        "  ### Key skills + platforms in demand\n"
        "  ### Gaps to close (only those backed by demand ≥2 roles)\n"
        "  ### Trends (titles/skills/build-from/visas)\n\n"
        "Keep it scannable. Tables where useful. No preamble."
    )
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1800,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def synthesize_compare(client, israel_agg, intl_agg, israel_insights, intl_insights):
    """One Claude call to compare Israel vs International."""
    system = (
        "You are synthesising a side-by-side comparison for Lior. Highlight differences "
        "in titles, skills, visa friction, and build-from leverage. Concrete + concise."
    )
    prompt = (
        "ISRAEL bucket insights:\n\n"
        f"{israel_insights}\n\n---\n\nINTERNATIONAL bucket insights:\n\n{intl_insights}\n\n---\n\n"
        "Produce a markdown section titled '## Israel vs International — comparison' with:\n"
        "  - 3-5 sharp deltas between the two markets\n"
        "  - Which bucket favours Lior most strongly + why\n"
        "  - Actionable lean direction for the next 30 days\n"
        "Tables welcome. No preamble."
    )
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def run():
    if not NOTION_TOKEN or not ANTHROPIC_API_KEY:
        print("ERROR: missing NOTION_TOKEN or ANTHROPIC_API_KEY")
        sys.exit(1)
    notion = Client(auth=NOTION_TOKEN)
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    print("Fetching all Sourced Roles...")
    roles = fetch_all_roles(notion)
    print(f"  {len(roles)} roles loaded")

    # Bucket
    israel = [r for r in roles if bucket_for_location(r["location"]) == "israel"]
    intl = [r for r in roles if bucket_for_location(r["location"]) == "international"]
    unknown = [r for r in roles if bucket_for_location(r["location"]) == "unknown"]
    print(f"  Israel: {len(israel)}  International: {len(intl)}  Unknown location: {len(unknown)}")

    overall_agg = aggregate(roles, "Overall")
    israel_agg = aggregate(israel, "Israel")
    intl_agg = aggregate(intl, "International (anywhere not Israel)")

    overall_gaps = gap_by_demand(overall_agg)
    israel_gaps = gap_by_demand(israel_agg)
    intl_gaps = gap_by_demand(intl_agg)

    print("Synthesising insights via Claude (3 calls + 1 comparison)...")
    overall_md = synthesize_insights(client, overall_agg, overall_gaps, "Overall")
    israel_md = synthesize_insights(client, israel_agg, israel_gaps, "Israel")
    intl_md = synthesize_insights(client, intl_agg, intl_gaps, "International")
    compare_md = synthesize_compare(client, israel_agg, intl_agg, israel_md, intl_md)

    today = date.today().isoformat()
    reports_dir = Path(__file__).parent / "reports"
    reports_dir.mkdir(exist_ok=True)

    json_out = {
        "generated": today,
        "buckets": {
            "overall": overall_agg,
            "israel": israel_agg,
            "international": intl_agg,
        },
        "gaps": {
            "overall": overall_gaps,
            "israel": israel_gaps,
            "international": intl_gaps,
        },
    }
    (reports_dir / f"insights_{today}.json").write_text(json.dumps(json_out, indent=2))
    print(f"Wrote reports/insights_{today}.json")

    md = (
        f"# Sourced Roles — Insights ({today})\n\n"
        f"Buckets: **{israel_agg['n_roles']} Israel** · "
        f"**{intl_agg['n_roles']} International** · {len(unknown)} unknown location.\n\n"
        "---\n\n## Overall\n\n" + overall_md + "\n\n"
        "---\n\n## Israel\n\n" + israel_md + "\n\n"
        "---\n\n## International (anywhere not Israel)\n\n" + intl_md + "\n\n"
        "---\n\n" + compare_md + "\n"
    )
    (reports_dir / f"insights_{today}.md").write_text(md)
    print(f"Wrote reports/insights_{today}.md")
    return json_out


if __name__ == "__main__":
    run()
