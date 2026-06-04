"""Real-time observer agent.

Given a (original, edited) pair from any drafting agent + an optional comment
from Lior, the observer hypothesizes ONE preference rule that — if she
confirms it — should land in voice_corpus/lior_preferences.md.

Design choices that matter:

1. ONE rule per hypothesis. Not a list. The UI is propose-confirm-once;
   bundling rules into one hypothesis makes confirmation ambiguous.

2. Scope is part of the output. The observer guesses whether this is a
   universal rule, drafter-specific, or just this-case. Lior can override
   the scope before confirming.

3. Confidence is part of the output. Single revisions get "low" confidence
   unless explicitly stated by Lior. The UI surfaces this so she sees
   when the observer is hedging.

4. The observer's own reasoning is recorded as a trace too — meta-debugging.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anthropic import Anthropic

import config as _config  # noqa: F401 — loads .env

from .trace import Trace


REVISIONS_DIR = Path(__file__).resolve().parent / "revisions"


SYSTEM_PROMPT = """You are an observer watching Lior Mamon edit her AI agents' outputs.
Your job: read one (original_draft → edited_version) pair and propose ONE
preference rule she might want her agents to follow going forward.

You are NOT trying to find every possible rule. ONE clean, actionable rule.
Concrete, specific, and phrased so a drafting agent can apply it.

PRINCIPLES
- Specific > general. "Avoid em-dashes in opening lines" beats "be less formal".
- Phrase it as a directive a model can follow, not a description of what she did.
- If her edit removed something specific (a word, a phrase, a structure), the
  rule is "avoid X" or "prefer Y instead of X".
- If you can't extract a clean rule from this single revision, set
  confidence: "low" and explain.

CONFIDENCE RUBRIC
- "high"   : Lior left an explicit comment naming the rule, OR the edit shows
             an unambiguous pattern (e.g. she rewrote 3 separate sentences
             with the same kind of change).
- "medium" : The edit clearly removes/replaces something specific, but you
             can't be sure it's a general pattern from one example.
- "low"    : The edit is mixed (substance + style), or you're guessing at
             intent. Flag this honestly.

SCOPE RUBRIC (pick exactly one)
- "universal"   : Style/voice that applies to all of her agents' outputs.
- "researcher"  : Specific to research briefs (level of detail, citation style).
- "outreach"    : Specific to LinkedIn/email outreach (tone, opening, closing).
- "cv"          : CV bullet phrasing, ordering, content.
- "cover_letter": Cover letter prose.
- "other"       : Doesn't fit the standard scopes.

OUTPUT FORMAT — STRICT JSON, no preamble, no fences:
{
  "rule_text": "<the directive, one sentence>",
  "scope": "<one of the rubric values>",
  "confidence": "<high|medium|low>",
  "reasoning": "<2-3 sentences explaining the diff signal you read>",
  "context_examples": ["<short before>", "<short after>"]
}
"""


def _client() -> Anthropic:
    return Anthropic()


def _diff_summary(original: str, edited: str, max_len: int = 1200) -> str:
    """Produce a compact diff summary the observer can read.

    For V0 we just feed both texts — Claude is good at diffing prose. A real
    diff algorithm could come later if we hit context limits.
    """
    o = (original or "").strip()
    e = (edited or "").strip()
    if len(o) + len(e) <= max_len * 2:
        return f"ORIGINAL:\n{o}\n\nEDITED:\n{e}"
    return f"ORIGINAL (truncated):\n{o[:max_len]}\n\nEDITED (truncated):\n{e[:max_len]}"


def observe_revision(
    *,
    original: str,
    edited: str,
    source_agent: str,
    source_trace_id: str | None = None,
    lior_comment: str = "",
) -> dict[str, Any]:
    """Run the observer on one revision. Returns the hypothesis dict.

    Side effects:
      - Writes a revision record to agents/revisions/<timestamp>_<agent>.json
        with user_decision = "pending" until Lior confirms via UI.
      - Writes an observer trace to agents/traces/observer/...

    Caller (the UI) is responsible for calling confirm_rule() / reject_rule()
    after Lior decides.
    """
    trace = Trace.new(
        agent="observer",
        brief=f"Revision of {source_agent} draft" + (f" ({source_trace_id})" if source_trace_id else ""),
        subject_slug=source_agent,
    )
    trace.flush()

    user_message = (
        f"Source agent: {source_agent}\n"
        f"Lior's comment: {lior_comment or '(none provided)'}\n\n"
        f"{_diff_summary(original, edited)}\n\n"
        "Return the JSON hypothesis."
    )

    try:
        resp = _client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()

        # Best-effort cost tracking
        usage = getattr(resp, "usage", None)
        if usage is not None:
            from .trace import estimate_cost
            trace.tokens_in = getattr(usage, "input_tokens", 0) or 0
            trace.tokens_out = getattr(usage, "output_tokens", 0) or 0
            trace.cost_usd = estimate_cost(trace.tokens_in, trace.tokens_out)

        # Extract the JSON object
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            hypothesis = {
                "rule_text": "",
                "scope": "other",
                "confidence": "low",
                "reasoning": f"Observer returned non-JSON: {text[:200]}",
                "context_examples": [],
            }
        else:
            try:
                hypothesis = json.loads(m.group(0))
            except json.JSONDecodeError as e:
                hypothesis = {
                    "rule_text": "",
                    "scope": "other",
                    "confidence": "low",
                    "reasoning": f"JSON parse error: {e}",
                    "context_examples": [],
                }

        trace.finish(status="completed", final_output=hypothesis)
    except Exception as e:
        trace.finish(status="error", error=f"{type(e).__name__}: {e}")
        hypothesis = {
            "rule_text": "",
            "scope": "other",
            "confidence": "low",
            "reasoning": f"Observer crashed: {e}",
            "context_examples": [],
        }

    # ---- Persist the revision record (decision pending) ---------------------
    REVISIONS_DIR.mkdir(parents=True, exist_ok=True)
    rev_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S") + f"_{source_agent}"
    rev_path = REVISIONS_DIR / f"{rev_id}.json"
    rev_path.write_text(json.dumps({
        "revision_id": rev_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_agent": source_agent,
        "source_trace_id": source_trace_id,
        "original": original,
        "edited": edited,
        "lior_comment": lior_comment,
        "observer_trace_id": trace.trace_id,
        "observer_hypothesis": hypothesis,
        "user_decision": "pending",
        "final_rule": None,
        "final_scope": None,
    }, indent=2))

    hypothesis["_revision_id"] = rev_id
    hypothesis["_observer_trace_id"] = trace.trace_id
    return hypothesis


def confirm_rule(
    revision_id: str,
    rule_text: str,
    scope: str,
) -> None:
    """Lior confirmed the rule (possibly edited from the hypothesis).

    Updates the revision record AND appends the rule to lior_preferences.md.
    """
    from . import preferences  # local to avoid circular
    rev_path = REVISIONS_DIR / f"{revision_id}.json"
    if not rev_path.exists():
        raise FileNotFoundError(f"No such revision: {revision_id}")
    data = json.loads(rev_path.read_text())
    data["user_decision"] = "confirmed"
    data["final_rule"] = rule_text.strip()
    data["final_scope"] = scope
    rev_path.write_text(json.dumps(data, indent=2))
    preferences.add_rule(scope=scope, rule_text=rule_text, source_revision_id=revision_id)


def reject_rule(revision_id: str, reason: str = "just_this_case") -> None:
    """Lior rejected the hypothesis. Reason is one of:
       just_this_case | wrong_inference | unclear
    """
    rev_path = REVISIONS_DIR / f"{revision_id}.json"
    if not rev_path.exists():
        raise FileNotFoundError(f"No such revision: {revision_id}")
    data = json.loads(rev_path.read_text())
    data["user_decision"] = "rejected"
    data["rejection_reason"] = reason
    rev_path.write_text(json.dumps(data, indent=2))
