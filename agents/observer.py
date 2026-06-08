"""Real-time observer agent — V1 (conversational + structured).

V0 was binary: observer proposed one rule, Lior clicked Confirm / Reject.
That collapsed nuance. A real preference has a principle, contexts where
it applies, contexts where it doesn't, and a specific actionable directive.
V0 also gave Lior no path to refine — only accept or discard whole.

V1 changes:

1. STRUCTURED HYPOTHESIS, not a flat rule. Output schema:
     {
       "principle": "why this matters — the underlying value",
       "applies_when": [contexts where this is right],
       "doesnt_apply_when": [contexts where this would be wrong],
       "specific_rule": "actionable directive for the drafter",
       "reasoning": "what in the diff led to this inference",
       "confidence": "high|medium|low",
       "clarifying_questions": ["ONE concrete question to sharpen the rule"],
     }

2. CONVERSATIONAL REFINEMENT. After round 1, Lior can push back ("only for
   cold outreach, not warm reconnects"). The observer takes the conversation
   history + new context and revises. Repeat until convergence. She accepts
   the current version at any turn.

3. CONTEXT-AWARE STORAGE. preferences.add_rule() now takes the full structure,
   not a flat string. Drafters load applies_when / doesnt_apply_when so they
   know when to follow vs ignore each rule.

The observer's own conversation is itself traced — meta-debuggable.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anthropic import Anthropic

import config as _config  # noqa: F401 — loads .env

from .trace import Trace, estimate_cost


REVISIONS_DIR = Path(__file__).resolve().parent / "revisions"


# ---- System prompts --------------------------------------------------------

PROPOSE_SYSTEM_PROMPT = """You observe Lior Mamon revising her AI agents' outputs.
Your job: produce ONE structured preference rule that captures the principle
behind her edit — not just the surface change.

PRINCIPLES OVER MECHANICS
A preference has four layers:
  1. PRINCIPLE — the underlying value (e.g. "lead with evidence of attention").
  2. APPLIES WHEN — the contexts where the principle is right.
  3. DOESN'T APPLY WHEN — contexts where applying it would be wrong.
  4. SPECIFIC RULE — the concrete directive a drafter can follow.

Without contexts, rules over-apply. "Avoid formal openers" sounds smart until
the drafter writes "Hey careers@" on a hiring-policy inquiry. The applies_when
/ doesnt_apply_when fields prevent that failure mode.

CLARIFYING QUESTIONS
End every hypothesis with ONE concrete clarifying question that would sharpen
the rule's contexts. Examples:
  - "Does this apply to all outreach or only cold first touches?"
  - "Would this be right for a hiring-policy email too, or just LinkedIn DMs?"
  - "Is the em-dash issue about openings specifically, or anywhere in prose?"
DON'T ask vague questions ("does this seem right?"). Ask for the missing
context that turns a guess into a confident rule.

CONFIDENCE
  - "high"   : Lior left an explicit comment AND the edit is unambiguous.
  - "medium" : The edit shows a clear pattern but only one example.
  - "low"    : Mixed signal, or guessing at intent. Be honest.

SCOPE — pick exactly ONE:
  universal | researcher | outreach | cv | cover_letter | other

OUTPUT FORMAT — STRICT JSON, no preamble, no fences:
{
  "principle": "<one sentence — the underlying why>",
  "applies_when": ["<context 1>", "<context 2>"],
  "doesnt_apply_when": ["<context 1>"],
  "specific_rule": "<actionable directive>",
  "reasoning": "<2-3 sentences explaining what in the diff led to this>",
  "confidence": "high|medium|low",
  "clarifying_questions": ["<ONE concrete question>"],
  "scope": "universal|researcher|outreach|cv|cover_letter|other"
}
"""


REFINE_SYSTEM_PROMPT = """You are continuing a refinement conversation with Lior
about a preference rule you hypothesized. She has pushed back, added context,
or asked you to revise.

Your job: produce an UPDATED structured hypothesis that incorporates her
feedback. Same schema as before:

{
  "principle": "...",
  "applies_when": [...],
  "doesnt_apply_when": [...],
  "specific_rule": "...",
  "reasoning": "How you updated the rule given her feedback — 1-2 sentences",
  "confidence": "high|medium|low",
  "clarifying_questions": ["ONE follow-up if anything is still unclear, otherwise empty array"],
  "scope": "universal|researcher|outreach|cv|cover_letter|other"
}

PRINCIPLES OF REFINEMENT
- LISTEN. If she said "only for cold outreach", narrow applies_when accordingly.
- DON'T INVENT. If her feedback is specific, don't drift to a new topic.
- CONVERGE. Each round should be more confident than the last. If you're at
  high confidence and have no more clarifying questions, return clarifying_questions: [].
- STAY HONEST. If her feedback contradicts your earlier guess, say so in
  reasoning and update the rule.

Output ONLY the JSON object. No preamble, no fences.
"""


# ---- Helpers ---------------------------------------------------------------

def _client() -> Anthropic:
    return Anthropic()


def _diff_summary(original: str, edited: str, max_len: int = 1200) -> str:
    o = (original or "").strip()
    e = (edited or "").strip()
    if len(o) + len(e) <= max_len * 2:
        return f"ORIGINAL:\n{o}\n\nEDITED:\n{e}"
    return f"ORIGINAL (truncated):\n{o[:max_len]}\n\nEDITED (truncated):\n{e[:max_len]}"


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _empty_hypothesis(reason: str) -> dict:
    return {
        "principle": "",
        "applies_when": [],
        "doesnt_apply_when": [],
        "specific_rule": "",
        "reasoning": reason,
        "confidence": "low",
        "clarifying_questions": [],
        "scope": "other",
    }


def _load_revision(revision_id: str) -> dict:
    rev_path = REVISIONS_DIR / f"{revision_id}.json"
    if not rev_path.exists():
        raise FileNotFoundError(f"No such revision: {revision_id}")
    return json.loads(rev_path.read_text())


def _save_revision(revision_id: str, data: dict) -> None:
    rev_path = REVISIONS_DIR / f"{revision_id}.json"
    rev_path.write_text(json.dumps(data, indent=2))


# ---- Public API ------------------------------------------------------------

def propose_hypothesis(
    *,
    original: str,
    edited: str,
    source_agent: str,
    source_trace_id: str | None = None,
    lior_comment: str = "",
) -> dict[str, Any]:
    """Round 1 of the conversation: observer proposes a structured hypothesis.

    Side effects:
      - Writes a revision record to agents/revisions/<id>.json with the
        conversation initialized (round 1 = this hypothesis).
      - Writes an observer trace.

    Returns the hypothesis dict augmented with _revision_id + _round.
    """
    trace = Trace.new(
        agent="observer",
        brief=f"Round 1 — revision of {source_agent} draft" + (f" ({source_trace_id})" if source_trace_id else ""),
        subject_slug=source_agent,
    )
    trace.flush()

    user_message = (
        f"Source agent: {source_agent}\n"
        f"Lior's comment: {lior_comment or '(none provided)'}\n\n"
        f"{_diff_summary(original, edited)}\n\n"
        "Return the structured hypothesis as JSON."
    )

    hypothesis = _empty_hypothesis("observer not run yet")
    try:
        resp = _client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=PROPOSE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()

        usage = getattr(resp, "usage", None)
        if usage is not None:
            trace.tokens_in = getattr(usage, "input_tokens", 0) or 0
            trace.tokens_out = getattr(usage, "output_tokens", 0) or 0
            trace.cost_usd = estimate_cost(trace.tokens_in, trace.tokens_out)

        parsed = _extract_json(text)
        if parsed:
            hypothesis = {**_empty_hypothesis(""), **parsed}
            hypothesis["reasoning"] = parsed.get("reasoning", "")
        else:
            hypothesis = _empty_hypothesis(f"Observer returned non-JSON: {text[:200]}")

        trace.finish(status="completed", final_output=hypothesis)
    except Exception as e:
        trace.finish(status="error", error=f"{type(e).__name__}: {e}")
        hypothesis = _empty_hypothesis(f"Observer crashed: {e}")

    # ---- Persist revision record with conversation initialized -------------
    REVISIONS_DIR.mkdir(parents=True, exist_ok=True)
    rev_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S") + f"_{source_agent}"
    record = {
        "revision_id": rev_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_agent": source_agent,
        "source_trace_id": source_trace_id,
        "original": original,
        "edited": edited,
        "lior_comment": lior_comment,
        "conversation": [
            {
                "round": 1,
                "observer_trace_id": trace.trace_id,
                "hypothesis": hypothesis,
                "lior_response": None,  # filled by refine_hypothesis or accept_hypothesis
            }
        ],
        "user_decision": "pending",
        "final_rule": None,
    }
    _save_revision(rev_id, record)

    hypothesis["_revision_id"] = rev_id
    hypothesis["_round"] = 1
    return hypothesis


def refine_hypothesis(
    *,
    revision_id: str,
    lior_response: str,
) -> dict[str, Any]:
    """Round 2+ of the conversation. Re-runs the observer with full history
    + Lior's new feedback. Returns the revised hypothesis.
    """
    record = _load_revision(revision_id)
    rounds = record["conversation"]
    last_round = rounds[-1]
    last_round["lior_response"] = lior_response.strip()
    next_round_n = len(rounds) + 1

    trace = Trace.new(
        agent="observer",
        brief=f"Round {next_round_n} — refinement on {record['source_agent']} revision",
        subject_slug=record["source_agent"],
    )
    trace.flush()

    # Build the conversation history for the model
    user_message = (
        f"Source agent: {record['source_agent']}\n"
        f"Lior's original comment: {record.get('lior_comment') or '(none provided)'}\n\n"
        f"{_diff_summary(record['original'], record['edited'])}\n\n"
        f"--- Conversation history ---\n"
    )
    for r in rounds:
        user_message += (
            f"Round {r['round']} hypothesis:\n"
            f"{json.dumps(r['hypothesis'], indent=2)}\n\n"
            f"Lior's response: {r.get('lior_response') or '(continuing)'}\n\n"
        )
    user_message += (
        "Produce the updated JSON hypothesis incorporating Lior's most recent response."
    )

    hypothesis = _empty_hypothesis("not run")
    try:
        resp = _client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=REFINE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()

        usage = getattr(resp, "usage", None)
        if usage is not None:
            trace.tokens_in = getattr(usage, "input_tokens", 0) or 0
            trace.tokens_out = getattr(usage, "output_tokens", 0) or 0
            trace.cost_usd = estimate_cost(trace.tokens_in, trace.tokens_out)

        parsed = _extract_json(text)
        if parsed:
            hypothesis = {**_empty_hypothesis(""), **parsed}
            hypothesis["reasoning"] = parsed.get("reasoning", "")
        else:
            hypothesis = _empty_hypothesis(f"Observer returned non-JSON: {text[:200]}")
        trace.finish(status="completed", final_output=hypothesis)
    except Exception as e:
        trace.finish(status="error", error=f"{type(e).__name__}: {e}")
        hypothesis = _empty_hypothesis(f"Observer crashed: {e}")

    # Append the new round
    rounds.append({
        "round": next_round_n,
        "observer_trace_id": trace.trace_id,
        "hypothesis": hypothesis,
        "lior_response": None,
    })
    _save_revision(revision_id, record)

    hypothesis["_revision_id"] = revision_id
    hypothesis["_round"] = next_round_n
    return hypothesis


def accept_hypothesis(
    *,
    revision_id: str,
    final_hypothesis: dict,
) -> None:
    """Lior accepted the current hypothesis (possibly edited inline).

    Updates the revision record AND appends the structured rule to
    voice_corpus/lior_preferences.md via preferences.add_rule().
    """
    from . import preferences  # local to avoid circular
    record = _load_revision(revision_id)
    record["user_decision"] = "confirmed"
    record["final_rule"] = final_hypothesis
    _save_revision(revision_id, record)

    preferences.add_rule(
        scope=final_hypothesis.get("scope", "universal"),
        principle=final_hypothesis.get("principle", ""),
        applies_when=final_hypothesis.get("applies_when", []),
        doesnt_apply_when=final_hypothesis.get("doesnt_apply_when", []),
        specific_rule=final_hypothesis.get("specific_rule", ""),
        confidence=final_hypothesis.get("confidence", "medium"),
        source_revision_id=revision_id,
        rounds=len(record["conversation"]),
    )


def reject_revision(revision_id: str, reason: str = "just_this_case") -> None:
    """Save the revision as data only — no rule extracted."""
    record = _load_revision(revision_id)
    record["user_decision"] = "rejected"
    record["rejection_reason"] = reason
    _save_revision(revision_id, record)
