"""CV / cover letter drafter — V0.

Single-shot drafter for one role at a time. Reads:
  - The role description (pasted text or loaded from Notion later)
  - Lior's profile (config.LIOR_PROFILE)
  - Skills inventory (SKILLS.md)
  - Optional: per-company research from reports/companies/{slug}.json
  - Learned preferences (voice_corpus/lior_preferences.md, scopes:
    universal + cv + cover_letter)

Outputs ONE structured JSON via the finalize tool:
  {
    "company_name": str,
    "role_title": str,
    "cv_changes": [
      {section, type, original, tailored, reasoning}, ...
    ],
    "cover_letter": "full text, ready to send",
    "honest_gaps": ["gaps to acknowledge or skip"]
  }

The trace appears in the Streamlit "Agent Runs" tab. The revision capture
flow there lets Lior paste her cowork-refined version + the cowork
conversation as context, and the observer learns from the diff.

Cost discipline:
  - This module makes ZERO API calls at import time.
  - draft() makes exactly one Anthropic call inside the runner loop
    (the model calls finalize once, runner exits).
  - My math per draft: ~5k input + ~1.5k output on Sonnet 4.6
    = $0.015 + $0.0225 = ~$0.04 actual.
    With the 3x quote rule = ~$0.12 quoted ceiling.
"""
import json
import re
from datetime import date
from pathlib import Path

from .runner import run_agent
from .trace import Trace
from . import preferences

# Import config lazily to avoid loading env at module-load time when only
# reading types from this file. The runner already imports config.
import config


# ---- Resource loaders (no API) ---------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_PATH = _REPO_ROOT / "SKILLS.md"


def _load_skills() -> str:
    if _SKILLS_PATH.exists():
        return _SKILLS_PATH.read_text()
    return ""


def _slugify_company(name: str) -> str:
    s = (name or "").lower().strip()
    s = re.sub(r"\s*\(.*?\)\s*", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unnamed"


def _load_company_research(company_name: str) -> dict | None:
    """Find per-company research if it exists. Returns None on absence/error."""
    if not company_name:
        return None
    slug = _slugify_company(company_name)
    path = _REPO_ROOT / "reports" / "companies" / f"{slug}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        # Skip errored Phase 3 reports — they have research:{error: ...}
        research = data.get("research") or {}
        if "error" in research:
            return None
        if not research:
            return None
        return {
            "research": research,
            "warm_intros": data.get("warm_intros") or [],
            "intro_count": data.get("intro_count") or 0,
            "generated": data.get("generated"),
        }
    except Exception:
        return None


# ---- System prompt ---------------------------------------------------------

SYSTEM_PROMPT_BASE = """You are a careful CV / cover letter drafter for Lior Mamon.

YOUR JOB
Read the role description, the optional company research, Lior's profile,
and her skills inventory. Produce:
  1. A tailored CV diff — concrete suggested bullet edits, reorderings, or
     additions, NOT a full rewrite. Each change references a specific
     section and includes one-sentence reasoning tied to the role.
  2. A tailored cover letter — full text, 250-350 words, ready to send.
     Personal, specific, and grounded in Lior's actual experience.
  3. An honest list of gaps — areas where the role asks for something Lior
     doesn't have. The cover letter should either acknowledge these
     honestly or sidestep cleanly (never paper over them).

GROUND RULES
- Stay strictly inside the truth of Lior's documented experience. Never
  invent skills, projects, employers, or numbers. If she's "Learning" on
  a skill per SKILLS.md, do not claim "Proficient" in the cover letter.
- Honor the engineer-role anti-pattern from her profile: Lior is NOT an
  engineer applying for engineering-heavy roles. If the JD is heavily
  engineering, flag that in honest_gaps and shape the cover letter around
  her actual strengths (Creative AI, analytics, vibe-coding, agent
  workflows applied to real-world problems).
- Lead the cover letter with concrete company-specific signal in line 1
  (a recent launch, news, research direction) when the company research
  provides one. Generic openers ("I hope this finds you well", "I am
  writing to express my interest...") are forbidden.
- Cite the warm intro by name in the cover letter IF the company research
  surfaced a relevant warm contact AND the role is a plausible match for
  that contact's department.
- Match the company's voice. A tech-creative co like Lightricks reads a
  different register than a bank. Use the company research to calibrate.

PROCESS
You will have all inputs in the user turn below. Think through them, then
call the finalize tool exactly ONCE with the structured JSON output. Do
not call finalize multiple times.

OUTPUT SCHEMA (passed to finalize)
{
  "company_name": "...",
  "role_title": "...",
  "cv_changes": [
    {
      "section": "Summary | Experience | Skills | Projects | Education | Other",
      "type": "add | edit | reorder | remove",
      "original": "...",     // empty string for 'add'
      "tailored": "...",     // empty string for 'remove'
      "reasoning": "one sentence — tie to the role"
    },
    ...
  ],
  "cover_letter": "Full 250-350 word text, ready to send",
  "honest_gaps": ["one-line each — where Lior doesn't fit the role"]
}
"""


# ---- Tools -----------------------------------------------------------------

def _make_tool_specs() -> list[dict]:
    return [
        {
            "name": "finalize",
            "description": (
                "Emit the final tailored CV diff + cover letter and end the run. "
                "Call this exactly ONCE. After this call, the agent terminates."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "company_name": {"type": "string"},
                    "role_title": {"type": "string"},
                    "cv_changes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "section": {"type": "string"},
                                "type": {
                                    "type": "string",
                                    "enum": ["add", "edit", "reorder", "remove"],
                                },
                                "original": {"type": "string"},
                                "tailored": {"type": "string"},
                                "reasoning": {"type": "string"},
                            },
                            "required": ["section", "type", "reasoning"],
                        },
                    },
                    "cover_letter": {"type": "string"},
                    "honest_gaps": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "company_name",
                    "role_title",
                    "cv_changes",
                    "cover_letter",
                    "honest_gaps",
                ],
            },
        },
    ]


class _Result:
    """Tiny capture for the finalize() tool result.

    The runner's tool-call infrastructure expects a callable that takes the
    parsed input dict and returns a JSON-serialisable result. We just stash
    the structured output here and return an ack.
    """
    def __init__(self):
        self.final: dict | None = None
        self.calls: int = 0

    def finalize(self, payload: dict) -> dict:
        self.calls += 1
        if self.final is None:
            self.final = payload
        return {"finalized": True, "n_calls": self.calls}


# ---- Public API ------------------------------------------------------------

def draft(
    *,
    role_text: str,
    company_name: str,
    company_research: dict | None = None,
    max_iterations: int = 3,
) -> Trace:
    """Draft tailored CV diff + cover letter for one role.

    Args:
      role_text: JD / role description as plain text. Paste the full posting.
      company_name: used for trace slugging and to load per-company research
        from reports/companies/{slug}.json if no override provided.
      company_research: pre-loaded company research dict (the shape returned
        by _load_company_research). If None, attempt to load by company_name.
      max_iterations: safety cap on the model's tool-use loop. Should never
        exceed 1-2 in practice since finalize ends the run.

    Returns:
      Trace with status="completed" and final_output set to the structured
      drafter JSON when successful.
    """
    if not role_text or not role_text.strip():
        raise ValueError("role_text is required")
    if not company_name or not company_name.strip():
        raise ValueError("company_name is required")

    # Resolve optional company research
    research_blob = company_research
    if research_blob is None:
        research_blob = _load_company_research(company_name)

    # Build the system prompt: base + profile + skills + learned preferences
    skills_md = _load_skills()
    prefs_block = preferences.render_for_system_prompt(
        ["universal", "cv", "cover_letter"]
    )

    system_prompt_parts = [SYSTEM_PROMPT_BASE]
    if getattr(config, "LIOR_PROFILE", None):
        system_prompt_parts.append("\n## LIOR_PROFILE\n" + config.LIOR_PROFILE)
    if skills_md:
        system_prompt_parts.append("\n## SKILLS.md\n" + skills_md)
    if prefs_block:
        system_prompt_parts.append(prefs_block)
    system_prompt = "\n".join(system_prompt_parts)

    # Build the brief (user message): role + company research
    brief_parts = [
        f"Tailor a CV diff + cover letter for the following role at "
        f"**{company_name}**. Today's date: {date.today().isoformat()}.",
        f"\n## ROLE DESCRIPTION\n{role_text.strip()}",
    ]
    if research_blob:
        research_json = json.dumps(research_blob, indent=2, default=str)
        # Cap to keep token count predictable
        brief_parts.append(
            f"\n## COMPANY RESEARCH (from reports/companies/)\n"
            f"```json\n{research_json[:5000]}\n```"
        )
    brief_parts.append(
        "\nCall the `finalize` tool exactly once with the structured output."
    )
    brief = "\n".join(brief_parts)

    # Run the agent
    result = _Result()
    trace = run_agent(
        agent_name="cv_tailor",
        brief=brief,
        system_prompt=system_prompt,
        tool_specs=_make_tool_specs(),
        tool_handlers={"finalize": result.finalize},
        subject_slug=company_name,
        max_iterations=max_iterations,
    )

    # Promote the captured structured output onto the trace's final_output
    if result.final is not None:
        trace.final_output = result.final
        trace.flush()

    return trace


# ---- CLI -------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Draft a tailored CV diff + cover letter for one role."
    )
    parser.add_argument(
        "--role-file",
        required=True,
        help="Path to a text file containing the role description / JD",
    )
    parser.add_argument(
        "--company",
        required=True,
        help="Company name (used for trace + research lookup)",
    )
    parser.add_argument(
        "--no-research",
        action="store_true",
        help="Skip loading per-company research even if present",
    )
    args = parser.parse_args()

    role_path = Path(args.role_file)
    if not role_path.exists():
        print(f"role file not found: {role_path}")
        raise SystemExit(2)
    role_text = role_path.read_text()

    # Allow explicit research suppression for testing
    research = None
    if not args.no_research:
        research = _load_company_research(args.company)

    print(f"Drafting for {args.company}... (this is one billable API call)")
    trace = draft(
        role_text=role_text,
        company_name=args.company,
        company_research=research,
    )

    print(f"\nSTATUS: {trace.status}")
    print(f"ITERATIONS: {len(trace.iterations)}")
    print(f"COST: ${trace.cost_usd:.4f}")
    print(f"TRACE: {trace.path()}")
    if trace.final_output:
        print("\n--- OUTPUT (first 2000 chars) ---")
        print(json.dumps(trace.final_output, indent=2)[:2000])
