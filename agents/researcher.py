"""Research agent V0 — adaptive depth, with a notebook.

Replaces the static-prompt company research in company_research.py with a
loop that:
  1. Reads a brief (e.g. "deep dive on Lightricks for an AI Creative Specialist")
  2. Plans its own queries (no hardcoded prompts)
  3. Searches the web via Anthropic's built-in web_search tool
  4. Takes structured notes to an in-memory notebook (visible in trace)
  5. Decides when to stop — finalize() is the only way out, agent must
     emit the final structured brief before terminating

Why this beats the static script:
  - Sleepy stable company = 1-2 searches
  - Acquisition-rumor company = 4-5 searches with follow-ups
  - Depth matches signal, not a hardcoded loop

The notebook is the key abstraction. It's the agent's working memory,
visible to the agent (via read_notebook) and to you (via the trace).
"""
import json
from datetime import date

from anthropic import Anthropic

from .runner import run_agent
from .trace import Trace


# ---------- System prompt ---------------------------------------------------
SYSTEM_PROMPT = """You are a research agent supporting an AI Creative Specialist's
job hunt (Lior Mamon — Tel Aviv, ex-Artlist, built Sliding Doors LoRA on Flux 2 Klein,
hunts roles at the intersection of Creative AI, Creative Technology, Product Analytics,
and Vibe Coding).

YOUR JOB: produce one structured research brief on the target the user names.

HOW YOU WORK:
  1. Before each tool call, briefly state your reasoning in 1-2 sentences.
     This is for the human watching the trace — they want to see your thinking.
  2. Use web_search to gather fresh signal (June 2026 preferred).
  3. Use take_note() to record findings as you go — this is your working memory.
     One note per atomic finding, with a source URL when possible.
  4. Use read_notebook() if you need to see what you've gathered so far.
  5. When you have enough confidence — typically 3-6 notes covering status,
     recent news, hiring signal, and Lior-fit — call finalize() with the
     structured brief. finalize() ends the run.

KEEP IT TIGHT:
  - Don't search the same thing twice. Read your notebook first.
  - If two searches return the same info, stop searching, finalize.
  - Skip filler. "Founded in 2015" doesn't help a job seeker.
  - Lior-fit matters: tie findings to her threads (Creative AI, LoRA,
    open models, vibe coding, agents) when relevant.

OUTPUT SCHEMA for finalize():
{
  "status_summary": "1 sentence: hiring / paused / layoffs / acquired",
  "hiring_policy": "1-2 sentences: visa sponsorship, remote, EOR-friendly",
  "recent_news": ["bullet", "bullet", "bullet"],
  "growth_signal": "1 sentence: headcount/funding/product trajectory",
  "fit_for_lior": "1 sentence: which of her threads this aligns with",
  "sources": ["url1", "url2", ...]
}

You have at most 8 iterations total. Budget accordingly.
"""


# ---------- Tools -----------------------------------------------------------

# Note: web_search is Anthropic's built-in tool, declared with the special type.
# The other three are local Python callables.
def _make_tool_specs() -> list[dict]:
    return [
        {
            "type": "web_search_20250305",
            "name": "web_search",
        },
        {
            "name": "take_note",
            "description": (
                "Save a finding to the notebook. Use one note per atomic finding. "
                "Include a source URL when you have one. Categorize the note so the "
                "human can scan the notebook later."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["status", "hiring", "news", "growth", "fit", "other"],
                        "description": "Which part of the final brief this supports.",
                    },
                    "finding": {
                        "type": "string",
                        "description": "One-sentence atomic finding.",
                    },
                    "source": {
                        "type": "string",
                        "description": "Source URL if available. Empty string if synthesis.",
                    },
                },
                "required": ["category", "finding"],
            },
        },
        {
            "name": "read_notebook",
            "description": (
                "Returns all notes you've taken so far, grouped by category. "
                "Use this before deciding whether to search again — you may "
                "already have what you need."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "finalize",
            "description": (
                "Emit the final structured research brief and end the run. "
                "Only call this once you have enough notes. After this call, "
                "the agent terminates."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "status_summary": {"type": "string"},
                    "hiring_policy": {"type": "string"},
                    "recent_news": {"type": "array", "items": {"type": "string"}},
                    "growth_signal": {"type": "string"},
                    "fit_for_lior": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "status_summary",
                    "hiring_policy",
                    "recent_news",
                    "growth_signal",
                    "fit_for_lior",
                    "sources",
                ],
            },
        },
    ]


class _Notebook:
    """The agent's working memory. Visible in trace AND to the agent."""
    def __init__(self):
        self.notes: list[dict] = []
        self.final: dict | None = None

    def take(self, category: str, finding: str, source: str = "") -> dict:
        entry = {"category": category, "finding": finding, "source": source}
        self.notes.append(entry)
        return {"saved": True, "total_notes": len(self.notes)}

    def read(self) -> dict:
        grouped: dict[str, list[dict]] = {}
        for n in self.notes:
            grouped.setdefault(n["category"], []).append(n)
        return {"total_notes": len(self.notes), "by_category": grouped}

    def finalize(self, brief: dict) -> dict:
        self.final = brief
        return {"finalized": True}


def research(target: str, *, max_iterations: int = 8) -> Trace:
    """Run the research agent on one target (company name, topic, etc.).

    Returns the completed Trace. The structured brief is at trace.final_output
    when status == "completed".
    """
    nb = _Notebook()

    handlers = {
        "take_note": lambda inp: nb.take(
            category=inp.get("category", "other"),
            finding=inp.get("finding", ""),
            source=inp.get("source", ""),
        ),
        "read_notebook": lambda inp: nb.read(),
        "finalize": lambda inp: nb.finalize(inp),
        # web_search is handled by Anthropic server-side; runner won't call us
        # for it. But declaring a no-op handler keeps the runner happy if a
        # newer SDK changes that behavior.
        "web_search": lambda inp: {"note": "handled server-side"},
    }

    brief = (
        f"Research target: {target}\n"
        f"As of {date.today().isoformat()}. "
        f"Produce the structured brief by calling finalize() when ready."
    )

    trace = run_agent(
        agent_name="researcher",
        brief=brief,
        system_prompt=SYSTEM_PROMPT,
        tool_specs=_make_tool_specs(),
        tool_handlers=handlers,
        subject_slug=target,
        max_iterations=max_iterations,
    )

    # The final brief lives in the notebook; promote it onto the trace so the
    # UI doesn't have to know about the notebook abstraction.
    if nb.final is not None:
        trace.final_output = {
            "brief": nb.final,
            "notebook": nb.read(),
        }
        trace.flush()
    return trace


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "Lightricks"
    t = research(target)
    print(f"\nSTATUS: {t.status}")
    print(f"ITERATIONS: {len(t.iterations)}")
    print(f"COST: ${t.cost_usd:.4f}")
    print(f"TRACE: {t.path()}")
    if t.final_output and isinstance(t.final_output, dict):
        print("\n--- BRIEF ---")
        print(json.dumps(t.final_output.get("brief"), indent=2))
