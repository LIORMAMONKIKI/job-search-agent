"""Preference store — reads/writes voice_corpus/lior_preferences.md.

Schema (markdown — human-readable + git-diffable + agent-loadable):

  ## <scope>
  - <rule text>  _(added YYYY-MM-DD, source revision: <id>)_

Scopes (closed set):
  - universal : applies to every drafter
  - researcher: research agent
  - outreach  : LinkedIn/email outreach
  - cv        : CV tailoring
  - cover_letter
  - other     : free-text scope (rare)

The file is the source of truth. add_rule() appends; remove_rule() rewrites.
"""
import re
from datetime import date
from pathlib import Path


PREFS_PATH = Path(__file__).resolve().parent.parent / "voice_corpus" / "lior_preferences.md"

VALID_SCOPES = ("universal", "researcher", "outreach", "cv", "cover_letter", "other")


def _read() -> str:
    if not PREFS_PATH.exists():
        return ""
    return PREFS_PATH.read_text()


def _write(text: str) -> None:
    PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFS_PATH.write_text(text)


def list_rules(scope: str | None = None) -> dict[str, list[str]]:
    """Return {scope: [rule_text, ...]}. If scope given, returns just that one."""
    text = _read()
    sections: dict[str, list[str]] = {s: [] for s in VALID_SCOPES}
    current = None
    for line in text.splitlines():
        m = re.match(r"^##\s+([a-z_]+)\s*$", line.strip())
        if m and m.group(1) in sections:
            current = m.group(1)
            continue
        if current and line.strip().startswith("- "):
            # Strip trailing metadata like "_(added ..., source ...)_"
            rule = re.sub(r"\s*_\(added.*?\)_\s*$", "", line.strip()[2:]).strip()
            if rule and not rule.startswith("_("):
                sections[current].append(rule)
    if scope:
        return {scope: sections.get(scope, [])}
    return sections


def add_rule(scope: str, rule_text: str, source_revision_id: str = "") -> None:
    """Append a rule under the given scope. Idempotent on (scope, rule_text)."""
    if scope not in VALID_SCOPES:
        scope = "other"
    rule_text = rule_text.strip().rstrip(".")
    if not rule_text:
        return
    # Skip duplicates within the same scope
    existing = list_rules(scope).get(scope, [])
    if any(rule_text.lower() == e.lower() for e in existing):
        return

    text = _read()
    if not text.strip():
        text = "# Lior's learned preferences (auto-managed)\n\n"
    # Make sure the scope header exists
    if not re.search(rf"^##\s+{re.escape(scope)}\s*$", text, re.MULTILINE):
        text += f"\n## {scope}\n_(no rules yet)_\n"

    today = date.today().isoformat()
    suffix = f"  _(added {today}"
    if source_revision_id:
        suffix += f", source revision: `{source_revision_id}`"
    suffix += ")_"
    rule_line = f"- {rule_text}{suffix}\n"

    # Append the rule under its section. If the section currently says
    # "(no rules yet)", we replace that placeholder.
    pattern = re.compile(
        rf"(^##\s+{re.escape(scope)}\s*$)(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    def repl(m):
        header = m.group(1)
        body = m.group(2)
        body = re.sub(r"_\(no rules yet\)_\s*\n", "", body)
        return f"{header}\n{body.rstrip()}\n{rule_line}\n"
    new_text = pattern.sub(repl, text, count=1)
    if new_text == text:
        # Section wasn't matched (edge case) — append at end
        new_text = text.rstrip() + f"\n\n## {scope}\n{rule_line}"
    _write(new_text)


def render_for_system_prompt(scopes: list[str]) -> str:
    """Render the relevant rules as a system-prompt-ready string.

    Pass scopes that apply to the drafting agent, e.g.
      render_for_system_prompt(["universal", "outreach"])
    Returns a section that can be appended to any agent's system prompt.
    Empty string if no rules.
    """
    seen = set()
    out_lines = []
    rules = list_rules()
    for scope in scopes:
        for r in rules.get(scope, []):
            key = r.lower()
            if key in seen:
                continue
            seen.add(key)
            out_lines.append(f"- ({scope}) {r}")
    if not out_lines:
        return ""
    return (
        "\n\n## Lior's learned preferences\n"
        "These rules were inferred from her past revisions. Honor them strictly:\n"
        + "\n".join(out_lines)
        + "\n"
    )
