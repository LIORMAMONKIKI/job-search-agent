"""Trace storage. JSON-on-disk is the source of truth; viewers read this.

A Trace is the full record of one agent run:
  {
    "trace_id": "research_lightricks_2026-06-04T14-30-12",
    "agent": "researcher",
    "brief": "...",
    "started_at": "ISO-8601",
    "ended_at": "ISO-8601",
    "status": "completed" | "error" | "max_iterations",
    "iterations": [Iteration, ...],
    "final_output": <agent-specific>,
    "cost_usd": float,
    "tokens_in": int,
    "tokens_out": int,
    "error": str | None,
  }

An Iteration is one round-trip with the model:
  {
    "n": 1,
    "thinking": "...visible reasoning text the model emitted...",
    "tool_calls": [{"name": ..., "input": {...}, "result": {...}}],
    "stop_reason": "tool_use" | "end_turn" | ...,
  }

Streamlit just reads these JSONs. So does the CLI viewer. So could LangSmith
later — the data layer is decoupled from any viewer.
"""
import json
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TRACES_ROOT = Path(__file__).resolve().parent.parent / "agents" / "traces"


def _slug(s: str, max_len: int = 50) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s or "untitled")[:max_len]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


@dataclass
class Iteration:
    n: int
    thinking: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    stop_reason: str | None = None


@dataclass
class Trace:
    trace_id: str
    agent: str
    brief: str
    started_at: str
    ended_at: str | None = None
    status: str = "running"
    iterations: list[Iteration] = field(default_factory=list)
    final_output: Any = None
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None

    @classmethod
    def new(cls, agent: str, brief: str, subject_slug: str | None = None) -> "Trace":
        sub = _slug(subject_slug or brief, max_len=40)
        trace_id = f"{agent}_{sub}_{_now_iso()}"
        return cls(
            trace_id=trace_id,
            agent=agent,
            brief=brief,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    def path(self) -> Path:
        d = TRACES_ROOT / self.agent
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{self.trace_id}.json"

    def flush(self) -> None:
        """Atomic write — write to tmp then rename so partial reads never happen."""
        p = self.path()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, default=str))
        tmp.replace(p)

    def finish(self, status: str, final_output: Any = None, error: str | None = None) -> None:
        self.status = status
        self.final_output = final_output
        self.error = error
        self.ended_at = datetime.now(timezone.utc).isoformat()
        self.flush()


# ---- Cost estimation -------------------------------------------------------
# Claude Sonnet 4.6 pricing (verify against Anthropic docs before relying on
# this for billing — used here only for ballpark cost display in the UI).
_PRICE_IN_PER_MTOK = 3.0
_PRICE_OUT_PER_MTOK = 15.0


def estimate_cost(tokens_in: int, tokens_out: int) -> float:
    return (
        tokens_in / 1_000_000 * _PRICE_IN_PER_MTOK
        + tokens_out / 1_000_000 * _PRICE_OUT_PER_MTOK
    )


# ---- Read side (for viewers) ----------------------------------------------
def list_traces(agent: str | None = None, limit: int = 50) -> list[dict]:
    """Return trace metadata (not full payload) for the UI list view."""
    root = TRACES_ROOT
    if not root.exists():
        return []
    files: list[Path] = []
    for sub in root.iterdir():
        if sub.is_dir() and (agent is None or sub.name == agent):
            files.extend(sub.glob("*.json"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text())
            out.append({
                "trace_id": data.get("trace_id"),
                "agent": data.get("agent"),
                "brief": data.get("brief", "")[:120],
                "status": data.get("status"),
                "started_at": data.get("started_at"),
                "ended_at": data.get("ended_at"),
                "iterations": len(data.get("iterations", [])),
                "cost_usd": data.get("cost_usd", 0.0),
                "path": str(f),
            })
        except Exception as e:
            out.append({"path": str(f), "error": str(e)})
    return out


def load_trace(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())
