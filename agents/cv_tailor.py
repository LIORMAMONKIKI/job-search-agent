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

# Lior's CV-process training material — lives OUTSIDE the repo (personal,
# not committed). Consolidated guide + the Wiz worked example.
_TRAINING_DIR = _REPO_ROOT.parent / "AGENT_TRAINING"
_TRAINING_FILES = [
    _TRAINING_DIR / "Lior_CV_Agent_Training.md",
    _TRAINING_DIR / "wiz_worked_example" / "STYLE_REASONING.md",
    _TRAINING_DIR / "wiz_worked_example" / "EDITING_PROCESS_Wiz_CV.md",
    _TRAINING_DIR / "wiz_worked_example" / "JOB_DESCRIPTION_Wiz.md",
]


def _load_skills() -> str:
    if _SKILLS_PATH.exists():
        return _SKILLS_PATH.read_text()
    return ""


def _load_training() -> str:
    """Concatenate the AGENT_TRAINING corpus. Empty string if absent —
    the drafter still works, just without the learned voice/process."""
    parts = []
    for p in _TRAINING_FILES:
        if p.exists():
            parts.append(f"\n### {p.name}\n\n{p.read_text()}")
    return "\n".join(parts)


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

You have been given her CV-PROCESS TRAINING MATERIAL below (consolidated
guide + the Wiz worked example). That material IS your instruction set —
read it as reasoning, not rulebook. The meta-rule there overrides
everything: every choice is a deliberate authored fit to ONE specific
role; never harden a one-off into a law.

YOUR JOB (follow her documented workflow)
  1. Read the role's signals — the JD, and the recruiter post if given.
     They often pull different directions; find what the role REALLY wants.
  2. Map every JD requirement → Strong / Adjacent / Gap against her real
     experience, with a framing decision per row. This map is part of
     your output — the reasoning must be auditable.
  3. Propose a TITLE — how she identifies herself, broad and true to her
     first, only a light lean toward the role. Not keyword bait. Kill
     echoes across adjacent words.
  4. Draft the ABSTRACT — her honest answer to what the JD is after, in
     her own true terms (never their words), ordered to foreground their
     priorities. Complete connected sentences with flow; no chopping, no
     narrative scaffolding. Title + abstract carry the voice — spend your
     effort there.
  5. Suggest EXPERIENCE / SKILLS edits — re-angle real bullets, reorder
     so the role's headline skill leads, thicken from documented facts.
     Never invent. Lead each role with its most role-relevant bullet.
  6. Draft the COVER LETTER (if the application calls for one) — 150-300
     words, in her voice per the training material, close casual
     ("Thanks,"). Grounded in what's true; gaps acknowledged in one
     honest sentence max or left alone, never over-explained.
  7. List HONEST GAPS — where she doesn't fit. Adjacent → named in real
     context. Gap → left alone on the CV; flagged here for her awareness.

HARD RULES (from her honesty guidelines — these are NOT vibe)
- Never invent skills, projects, employers, titles, or numbers.
- Never claim: ML/AI engineering, "designer" as identity/title,
  cross-functional-with-product-and-engineering, or anything she
  hasn't done. "Design foundations" is acceptable; "designer" is not.
- Honesty outranks keyword-matching. A JD-prioritized tool she only
  touched stays in the skills line as used — never dressed up as a
  strength.
- Use her real outcomes and numbers (from profile/skills/CV), not
  generic activity claims.

PROCESS
All inputs are in the user turn. Think through them, then call the
finalize tool exactly ONCE with the structured output.
"""


# ---- Tools -----------------------------------------------------------------

def _make_tool_specs() -> list[dict]:
    return [
        {
            "name": "finalize",
            "description": (
                "Emit the final tailored application bundle and end the run. "
                "Call this exactly ONCE. After this call, the agent terminates."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "company_name": {"type": "string"},
                    "role_title": {"type": "string"},
                    "title_suggestion": {
                        "type": "string",
                        "description": "CV subtitle/headline — identity-first, light lean to role",
                    },
                    "abstract": {
                        "type": "string",
                        "description": "CV summary — her honest answer to the JD's core ask, in her voice",
                    },
                    "matching_map": {
                        "type": "array",
                        "description": "JD requirement → fit + framing decision (auditable reasoning)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "requirement": {"type": "string"},
                                "fit": {
                                    "type": "string",
                                    "enum": ["Strong", "Adjacent", "Gap"],
                                },
                                "framing": {"type": "string"},
                            },
                            "required": ["requirement", "fit", "framing"],
                        },
                    },
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
                    "cover_letter": {
                        "type": "string",
                        "description": "Empty string if the application doesn't call for one",
                    },
                    "honest_gaps": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "company_name",
                    "role_title",
                    "title_suggestion",
                    "abstract",
                    "matching_map",
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
    current_cv_text: str = "",
    recruiter_post: str = "",
    company_research: dict | None = None,
    max_iterations: int = 3,
) -> Trace:
    """Draft a tailored application bundle for one role.

    Args:
      role_text: JD / role description as plain text. Paste the full posting.
      company_name: used for trace slugging and to load per-company research
        from reports/companies/{slug}.json if no override provided.
      current_cv_text: the CV she's editing FROM (plain text / markdown).
        Strongly recommended — without it the agent works off profile +
        skills only and can't propose surgical bullet edits.
      recruiter_post: optional recruiter LinkedIn post — often reveals the
        real priority vs the formal JD.
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

    # System prompt: base + training corpus + profile + skills + learned prefs
    skills_md = _load_skills()
    training = _load_training()
    prefs_block = preferences.render_for_system_prompt(
        ["universal", "cv", "cover_letter"]
    )

    system_prompt_parts = [SYSTEM_PROMPT_BASE]
    if training:
        system_prompt_parts.append(
            "\n## LIOR'S CV-PROCESS TRAINING MATERIAL\n" + training
        )
    if getattr(config, "LIOR_PROFILE", None):
        system_prompt_parts.append("\n## LIOR_PROFILE\n" + config.LIOR_PROFILE)
    if skills_md:
        system_prompt_parts.append("\n## SKILLS.md\n" + skills_md)
    if prefs_block:
        system_prompt_parts.append(prefs_block)
    system_prompt = "\n".join(system_prompt_parts)

    # Brief (user message): role + recruiter post + current CV + research
    brief_parts = [
        f"Tailor an application bundle for the following role at "
        f"**{company_name}**. Today's date: {date.today().isoformat()}.",
        f"\n## ROLE DESCRIPTION\n{role_text.strip()}",
    ]
    if recruiter_post.strip():
        brief_parts.append(f"\n## RECRUITER POST\n{recruiter_post.strip()}")
    if current_cv_text.strip():
        brief_parts.append(
            f"\n## CURRENT CV (edit FROM this — propose changes against it)\n"
            f"{current_cv_text.strip()}"
        )
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
        "--cv-file",
        default=None,
        help="Path to the current CV as text/markdown (edit-from baseline)",
    )
    parser.add_argument(
        "--recruiter-file",
        default=None,
        help="Path to a text file with the recruiter's LinkedIn post (optional)",
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

    cv_text = ""
    if args.cv_file:
        cv_path = Path(args.cv_file)
        if not cv_path.exists():
            print(f"cv file not found: {cv_path}")
            raise SystemExit(2)
        cv_text = cv_path.read_text()

    recruiter_text = ""
    if args.recruiter_file:
        rp = Path(args.recruiter_file)
        if rp.exists():
            recruiter_text = rp.read_text()

    # Allow explicit research suppression for testing
    research = None
    if not args.no_research:
        research = _load_company_research(args.company)

    print(f"Drafting for {args.company}... (this is one billable API call)")
    trace = draft(
        role_text=role_text,
        company_name=args.company,
        current_cv_text=cv_text,
        recruiter_post=recruiter_text,
        company_research=research,
    )

    print(f"\nSTATUS: {trace.status}")
    print(f"ITERATIONS: {len(trace.iterations)}")
    print(f"COST: ${trace.cost_usd:.4f}")
    print(f"TRACE: {trace.path()}")
    if trace.final_output:
        print("\n--- OUTPUT (first 2000 chars) ---")
        print(json.dumps(trace.final_output, indent=2)[:2000])
