"""Generic Claude tool-use loop with trace capture.

This is the heart of the agent layer. Every agent in this codebase calls
`run_agent(...)` with its own tools + system prompt + brief. The runner:
  1. Sends the brief to Claude with the declared tools
  2. If Claude calls tools → runs them, feeds results back, loops
  3. If Claude returns plain text → records as final output and stops
  4. Captures every iteration into a Trace (saved to disk on each step,
     so partial traces are inspectable even if the run crashes)

Designed to be model-agnostic-ish (we hardcode claude-sonnet-4-6 since
that's what the rest of the system uses), and framework-free — just the
raw Anthropic SDK + our own trace storage. No LangChain, no LangGraph.

Why hand-rolled instead of LangGraph: see CONVERSATION_NOTES — the loop
is ~50 lines of real logic and writing it ourselves makes every iteration
inspectable in the trace. LangGraph wraps the loop in a state machine
abstraction we don't need yet.
"""
import json
import time
from typing import Any, Callable

from anthropic import Anthropic

# Importing config triggers load_dotenv() so ANTHROPIC_API_KEY is in env.
# Keep this import at module-load time — every agent uses run_agent and
# expects the env to be set.
import config as _config  # noqa: F401

from .trace import Trace, Iteration, estimate_cost


# Tool spec — your agent declares tools like this:
#   {
#     "name": "take_note",
#     "description": "Save a finding to the notebook",
#     "input_schema": {"type": "object", "properties": {...}, "required": [...]}
#   }
# And a Python callable that takes (input_dict) → result_dict.
ToolHandler = Callable[[dict], Any]


DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_ITERATIONS = 8
DEFAULT_MAX_TOKENS = 2000


def run_agent(
    *,
    agent_name: str,
    brief: str,
    system_prompt: str,
    tool_specs: list[dict],
    tool_handlers: dict[str, ToolHandler],
    subject_slug: str | None = None,
    model: str = DEFAULT_MODEL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    client: Anthropic | None = None,
    on_iteration: Callable[[Trace, Iteration], None] | None = None,
) -> Trace:
    """Run an agent loop and return the completed Trace.

    Args:
      agent_name: e.g. "researcher", "outreach_drafter" — used for trace path.
      brief: the user-facing description of what this run is doing.
      system_prompt: the agent's persona + instructions.
      tool_specs: Anthropic tool-use schemas (list of dicts).
      tool_handlers: name → callable. Each callable takes the parsed input
        dict and returns a JSON-serialisable result.
      subject_slug: optional, makes trace_id readable
        (e.g. "lightricks" → research_lightricks_2026-06-04T14-30-12).
      max_iterations: safety cap. If reached, trace.status = "max_iterations".
      on_iteration: optional callback fired after each iteration is recorded
        (useful for streaming progress to a UI).

    Behavior:
      - Calls Anthropic. If stop_reason == "tool_use", runs the tools,
        appends results to message history, calls again.
      - Otherwise extracts the final text and finishes.
      - On any exception → trace.status = "error" with the exception string.
      - Trace is flushed to disk after EVERY iteration (no data loss on crash).
    """
    if client is None:
        client = Anthropic()

    trace = Trace.new(agent=agent_name, brief=brief, subject_slug=subject_slug)
    trace.flush()

    # Conversation state — what we replay back to Claude each turn.
    messages: list[dict] = [{"role": "user", "content": brief}]

    try:
        for i in range(1, max_iterations + 1):
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                tools=tool_specs,
                messages=messages,
            )

            # Accumulate token + cost counters (best-effort)
            usage = getattr(response, "usage", None)
            if usage is not None:
                trace.tokens_in += getattr(usage, "input_tokens", 0) or 0
                trace.tokens_out += getattr(usage, "output_tokens", 0) or 0
                trace.cost_usd = estimate_cost(trace.tokens_in, trace.tokens_out)

            # Pull the visible "thinking" text + any tool_use blocks
            thinking_chunks: list[str] = []
            tool_use_blocks: list[Any] = []
            for block in response.content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    thinking_chunks.append(block.text)
                elif btype == "tool_use":
                    tool_use_blocks.append(block)

            iteration = Iteration(
                n=i,
                thinking="\n".join(thinking_chunks).strip(),
                stop_reason=getattr(response, "stop_reason", None),
            )

            # Execute every tool call the model asked for this turn.
            for tu in tool_use_blocks:
                name = tu.name
                input_dict = dict(tu.input) if tu.input else {}
                handler = tool_handlers.get(name)
                if handler is None:
                    result = {"error": f"Unknown tool: {name}"}
                else:
                    try:
                        result = handler(input_dict)
                    except Exception as e:
                        result = {"error": f"Tool {name} crashed: {e}"}
                iteration.tool_calls.append({
                    "name": name,
                    "input": input_dict,
                    "result": result,
                })

            trace.iterations.append(iteration)
            trace.flush()
            if on_iteration:
                try:
                    on_iteration(trace, iteration)
                except Exception:
                    # Never let a UI callback kill the run.
                    pass

            # Decide whether to continue the loop
            stop_reason = iteration.stop_reason
            if stop_reason == "tool_use":
                # Feed assistant + tool_result messages back, loop again.
                messages.append({"role": "assistant", "content": response.content})
                tool_results_payload = []
                for tc, tu in zip(iteration.tool_calls, tool_use_blocks):
                    # Anthropic expects content as a string OR a list of blocks.
                    # We stringify the JSON result so the model reads it cleanly.
                    tool_results_payload.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(tc["result"], default=str)[:8000],
                    })
                messages.append({"role": "user", "content": tool_results_payload})
                continue

            # No more tool calls — extract final text and stop.
            final_text = "\n".join(thinking_chunks).strip()
            trace.finish(status="completed", final_output=final_text)
            return trace

        # Loop fell through max_iterations — record and return.
        trace.finish(
            status="max_iterations",
            final_output="\n".join(
                it.thinking for it in trace.iterations if it.thinking
            )[-2000:],
        )
        return trace

    except Exception as e:
        trace.finish(status="error", error=f"{type(e).__name__}: {e}")
        raise
