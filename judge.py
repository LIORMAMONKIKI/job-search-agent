"""Claude API judge — evaluates a role against Lior's skill thread + per-skill inventory.

System prompt = LIOR_PROFILE + SKILLS.md content + target titles + schema hint.
Cached via prompt_cache so the bigger prompt doesn't blow rate-limit budget.
"""
import json
import os
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, LIOR_PROFILE, TARGET_ROLE_TITLES


# Load SKILLS.md once at import time
_SKILLS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SKILLS.md")
try:
    with open(_SKILLS_PATH) as _f:
        _SKILLS_INVENTORY = _f.read()
except FileNotFoundError:
    _SKILLS_INVENTORY = ""


_RESPONSE_SCHEMA_HINT = """
Return ONLY a JSON object with this exact shape (no markdown, no commentary):

{
  "skill_match": "High" | "Medium" | "Low" | "Reject",
  "build_from_tags": ["Trajectory", "Brand", "Relocation", "Promotion"],
  "visa_likelihood": "Sponsors visa" | "EOR-friendly" | "Israel-local" | "L-1 lane" | "Multiple paths" | "Unknown",
  "application_friction": "Low" | "Medium" | "High",
  "matched_skills": ["ComfyUI", "LoRA training", ...],
  "gap_skills": ["LoRA dataset design", ...],
  "pitch_angle": "One actionable sentence: what to lead with in cover letter or DM.",
  "why_surfaced": "One short sentence about why this surfaced (or why low priority)."
}

Rules for grading:
- skill_match=Reject ONLY if: (a) clear no-fit (e.g., pure backend SWE with
  zero creative angle, ML Engineer / Research Scientist whose day-to-day is
  production code) OR (b) hard filter fails (US-only, no sponsorship, no remote).
- **Apply the Engineering-role anti-pattern from LIOR_PROFILE strictly.**
- Most roles should be Medium or Low — be picky on High.
- build_from_tags: include only tags that genuinely apply. Empty list is OK.
- visa_likelihood: infer from company hiring policy + role location wording.
- application_friction: Low if posted <7 days AND simple form; High if
  take-home or many rounds mentioned.

matched_skills / gap_skills / pitch_angle:
- matched_skills: up to 5 skills the JD explicitly asks for that Lior is
  Expert or Proficient at (per SKILLS.md). Use the EXACT skill names from
  SKILLS.md (e.g. "ComfyUI", "LoRA training", "Tableau"). Empty list if no
  real overlap.
- gap_skills: up to 3 skills the JD wants that are Lior's Familiar / Learning
  / Gap level. Skip if no real gap (she meets the bar). Skill at "Learning"
  level = mention as gap but note "actively building".
- pitch_angle: ONE actionable sentence. Lead with strongest match + specific
  artifact (e.g. "Lead with Sliding Doors LoRA + 4 yrs Artlist AI Creative
  ownership + Canva B2B curation methodology"). Skip generic advice.

- why_surfaced: ONE sentence, plainspoken, no fluff.
"""


def judge_role(role, company_context):
    """Score a role against Lior's skill thread + per-skill inventory.

    Args:
        role: dict from scraper with title, company, location, description,
              job_url, date_posted.
        company_context: dict with tier, hiring_policy, notes from Companies DB.

    Returns dict with skill_match / build_from_tags / visa_likelihood /
    application_friction / matched_skills / gap_skills / pitch_angle / why_surfaced.
    """
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    role_text = (
        f"Title: {role.get('title')}\n"
        f"Company: {role.get('company')}\n"
        f"Location: {role.get('location')}\n"
        f"Date Posted: {role.get('date_posted', 'unknown')}\n"
        f"JD Link: {role.get('job_url')}\n"
        f"Description (truncated):\n{(role.get('description') or '')[:3000]}\n"
    )

    company_text = (
        f"Tier: {company_context.get('tier')}\n"
        f"Hiring Policy: {company_context.get('hiring_policy')}\n"
        f"Company Notes: {company_context.get('notes')}\n"
    )

    target_titles_text = (
        "\n## Target role title patterns (semantic anchors — exact match not required)\n"
        + "\n".join(f"- {t}" for t in TARGET_ROLE_TITLES)
        + "\n\nRoles with titles that semantically match any of the above should "
        "be evaluated for fit. Roles with titles like 'Visual AI Engineer' or "
        "'Generative Content Lead' count even if not in the literal list."
    )

    skills_block = ""
    if _SKILLS_INVENTORY:
        skills_block = (
            "\n\n# Lior's full skills inventory (use for matched_skills/gap_skills)\n\n"
            + _SKILLS_INVENTORY
        )

    stable_system = (
        LIOR_PROFILE
        + target_titles_text
        + skills_block
        + "\n\n"
        + _RESPONSE_SCHEMA_HINT
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        system=[
            {
                "type": "text",
                "text": stable_system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{
            "role": "user",
            "content": f"COMPANY CONTEXT:\n{company_text}\n\nROLE:\n{role_text}\n\nReturn the JSON evaluation.",
        }],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "skill_match": "Low",
            "build_from_tags": [],
            "visa_likelihood": "Unknown",
            "application_friction": "Medium",
            "matched_skills": [],
            "gap_skills": [],
            "pitch_angle": "",
            "why_surfaced": f"Judge could not parse response: {raw[:200]}",
        }
