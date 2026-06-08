"""Preference store — structured rules in voice_corpus/lior_preferences.md.

V1 schema (markdown headers + structured field blocks). Each rule has:
  - principle           (the underlying why)
  - applies_when        (contexts where the rule is right)
  - doesnt_apply_when   (contexts where it would be wrong)
  - specific_rule       (actionable directive for the drafter)
  - confidence
  - source revision id + round count (audit trail in git)

Markdown structure:

  ## <scope>

  ### <short rule title>

  **Principle:** ...

  **Applies when:**
  - context
  - context

  **Does NOT apply when:**
  - context

  **Specific rule:** ...

  _(added YYYY-MM-DD, source revision: `id`, confirmed after N round(s))_

This file IS the source of truth. The drafter loads it. Lior reads + edits it
in any text editor. Git diff shows preference evolution over time.
"""
import re
from datetime import date
from pathlib import Path


PREFS_PATH = Path(__file__).resolve().parent.parent / "voice_corpus" / "lior_preferences.md"

VALID_SCOPES = ("universal", "researcher", "outreach", "cv", "cover_letter", "other")


# ---- File IO ---------------------------------------------------------------

def _read() -> str:
    if not PREFS_PATH.exists():
        return ""
    return PREFS_PATH.read_text()


def _write(text: str) -> None:
    PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFS_PATH.write_text(text)


def _ensure_skeleton() -> str:
    text = _read()
    if "# Lior's learned preferences" in text:
        return text
    header = (
        "# Lior's learned preferences (auto-managed)\n\n"
        "Confirmed rules accumulate here. Format and structure: see agents/preferences.py.\n\n"
        "---\n"
    )
    sections = "".join(f"\n## {s}\n_(no rules yet)_\n" for s in VALID_SCOPES)
    text = header + sections
    _write(text)
    return text


# ---- Title extraction ------------------------------------------------------

def _make_title(principle: str, specific_rule: str) -> str:
    """Short H3-friendly title from principle or rule. ~6-10 words."""
    src = principle or specific_rule or "Untitled rule"
    src = re.sub(r"^[\s\W]+|[\s\W]+$", "", src)
    words = src.split()
    if len(words) <= 8:
        return " ".join(words).rstrip(".")
    return " ".join(words[:8]).rstrip(".") + "…"


# ---- Add ------------------------------------------------------------------

def add_rule(
    *,
    scope: str,
    principle: str,
    applies_when: list[str],
    doesnt_apply_when: list[str],
    specific_rule: str,
    confidence: str = "medium",
    source_revision_id: str = "",
    rounds: int = 1,
) -> None:
    """Append a structured rule under the given scope. Idempotent on
    (scope, specific_rule) — same rule won't double-append.
    """
    if scope not in VALID_SCOPES:
        scope = "other"

    principle = (principle or "").strip().rstrip(".")
    specific_rule = (specific_rule or "").strip().rstrip(".")
    if not specific_rule and not principle:
        return

    existing_text = _ensure_skeleton()
    # Idempotency: skip if the same specific_rule already appears under the scope
    section_match = re.search(
        rf"(^##\s+{re.escape(scope)}\s*$)(.*?)(?=^##\s|\Z)",
        existing_text, re.MULTILINE | re.DOTALL,
    )
    if section_match and specific_rule and specific_rule.lower() in section_match.group(2).lower():
        return

    title = _make_title(principle, specific_rule)
    today = date.today().isoformat()
    bullets_apply = "\n".join(f"- {x}" for x in (applies_when or []) if x.strip())
    bullets_block = "\n".join(f"- {x}" for x in (doesnt_apply_when or []) if x.strip())
    audit = f"_(added {today}, source revision: `{source_revision_id}`, "
    audit += f"confidence: {confidence}, refined over {rounds} round(s))_"

    block = (
        f"\n### {title}\n\n"
        + (f"**Principle:** {principle}\n\n" if principle else "")
        + (f"**Applies when:**\n{bullets_apply}\n\n" if bullets_apply else "")
        + (f"**Does NOT apply when:**\n{bullets_block}\n\n" if bullets_block else "")
        + (f"**Specific rule:** {specific_rule}\n\n" if specific_rule else "")
        + f"{audit}\n"
    )

    # Insert at end of scope section (replacing "(no rules yet)" placeholder if present)
    if not section_match:
        new_text = existing_text.rstrip() + f"\n\n## {scope}\n{block}"
    else:
        header = section_match.group(1)
        body = section_match.group(2)
        body = re.sub(r"_\(no rules yet\)_\s*\n", "", body)
        new_section = f"{header}\n{body.rstrip()}\n{block}"
        new_text = existing_text[: section_match.start()] + new_section + existing_text[section_match.end():]
    _write(new_text)


# ---- Read ------------------------------------------------------------------

def list_rules(scope: str | None = None) -> dict[str, list[dict]]:
    """Return {scope: [rule_dict, ...]} parsed from the MD file.

    Each rule_dict has principle / applies_when / doesnt_apply_when /
    specific_rule / title.
    """
    text = _read()
    out: dict[str, list[dict]] = {s: [] for s in VALID_SCOPES}
    if not text:
        return {scope: out.get(scope, [])} if scope else out

    # Split into scope sections
    parts = re.split(r"^##\s+([a-z_]+)\s*$", text, flags=re.MULTILINE)
    # parts: [pre, scope1, body1, scope2, body2, ...]
    for i in range(1, len(parts), 2):
        sc = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        if sc not in out:
            continue

        # Now split body into rule blocks at "### " boundaries
        rule_blocks = re.split(r"^###\s+", body, flags=re.MULTILINE)
        for rb in rule_blocks[1:]:  # [0] is whatever sits before the first ###
            lines = rb.splitlines()
            title = lines[0].strip() if lines else ""
            rule = {
                "title": title,
                "principle": "",
                "applies_when": [],
                "doesnt_apply_when": [],
                "specific_rule": "",
            }
            current_field = None
            for ln in lines[1:]:
                m_principle = re.match(r"\*\*Principle:\*\*\s*(.*)$", ln)
                m_specific = re.match(r"\*\*Specific rule:\*\*\s*(.*)$", ln)
                if m_principle:
                    rule["principle"] = m_principle.group(1).strip()
                    current_field = None
                elif m_specific:
                    rule["specific_rule"] = m_specific.group(1).strip()
                    current_field = None
                elif ln.strip().startswith("**Applies when:"):
                    current_field = "applies_when"
                elif ln.strip().startswith("**Does NOT apply when:"):
                    current_field = "doesnt_apply_when"
                elif ln.strip().startswith("- ") and current_field:
                    rule[current_field].append(ln.strip()[2:].strip())
                elif ln.strip().startswith("_(") or ln.strip().startswith("**"):
                    current_field = None
            if rule["specific_rule"] or rule["principle"]:
                out[sc].append(rule)

    if scope:
        return {scope: out.get(scope, [])}
    return out


# ---- Render for drafter system prompts ------------------------------------

def render_for_system_prompt(scopes: list[str]) -> str:
    """Build the rules section for any drafting agent's system prompt.

    Each rule is rendered with its principle + contexts so the drafter
    knows WHEN to apply and WHEN not to. Empty string if no rules.
    """
    rules = list_rules()
    seen_specifics = set()
    lines: list[str] = []
    for scope in scopes:
        for r in rules.get(scope, []):
            key = (r.get("specific_rule") or r.get("title") or "").lower()
            if not key or key in seen_specifics:
                continue
            seen_specifics.add(key)
            chunks = [f"\n### ({scope}) {r.get('title') or 'Rule'}"]
            if r.get("principle"):
                chunks.append(f"  Principle: {r['principle']}")
            if r.get("applies_when"):
                aw = "; ".join(r["applies_when"])
                chunks.append(f"  Applies when: {aw}")
            if r.get("doesnt_apply_when"):
                nw = "; ".join(r["doesnt_apply_when"])
                chunks.append(f"  Does NOT apply when: {nw}")
            if r.get("specific_rule"):
                chunks.append(f"  Rule: {r['specific_rule']}")
            lines.extend(chunks)
    if not lines:
        return ""
    return (
        "\n\n## Lior's learned preferences\n"
        "These rules were inferred from Lior's revisions and confirmed by her.\n"
        "Honor each rule ONLY in its 'Applies when' contexts. If a context\n"
        "matches 'Does NOT apply when', explicitly do the opposite.\n"
        + "\n".join(lines)
        + "\n"
    )


# ---- Maintenance utility ---------------------------------------------------

def reset() -> None:
    """Wipe the prefs file back to skeleton. Useful for migrations / tests."""
    _write("")
    _ensure_skeleton()
