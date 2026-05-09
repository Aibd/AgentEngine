"""Offline smoke for chat_pretty.py - drives the renderer with mock SSE v2 frames."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
for p in (SCRIPTS, SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from rich.console import Console

from renderers.rich_renderer import ReasoningMode, RichRenderer


def frame(event: str, data: dict | None = None) -> dict:
    return {"event": event, "data": data or {}}


SCRIPT = [
    frame("start", {"query": "inspect src/agentengine"}),
    frame("step", {"turn": 1}),
    frame("thinking", {"delta": "Need to read the README first."}),
    frame(
        "tool_call_start",
        {
            "tool": "read_file",
            "arguments": {"path": "README.md"},
            "tool_call_id": "call_1",
        },
    ),
    frame(
        "tool_result",
        {
            "tool": "read_file",
            "result": "# AgentEngine Refactor\n\nStandalone scaffold ...",
            "ok": True,
            "elapsed_ms": 40,
            "tool_call_id": "call_1",
        },
    ),
    frame("step_end", {"turn": 1, "has_tool_calls": True, "elapsed_ms": 1200}),
    frame("step", {"turn": 2}),
    frame("thinking", {"delta": "README has enough context."}),
    frame("text", {"delta": "AgentEngine is a small runtime scaffold. "}),
    frame("text", {"delta": "It now emits **SSE v2** frames."}),
    frame("step_end", {"turn": 2, "has_tool_calls": False, "elapsed_ms": 800}),
    frame(
        "usage",
        {
            "prompt_tokens": 287,
            "completion_tokens": 64,
            "total_tokens": 351,
            "total_seconds": 2.0,
        },
    ),
    frame(
        "done",
        {
            "reason": "completed",
            "result": "AgentEngine now emits SSE v2 frames.",
        },
    ),
]


def main(reasoning: ReasoningMode = ReasoningMode.COLLAPSED) -> None:
    console = Console()
    renderer = RichRenderer(agent_name="deep_research", console=console, reasoning=reasoning)
    for evt in SCRIPT:
        renderer.handle(evt)
    renderer.finish()


if __name__ == "__main__":
    mode = ReasoningMode.COLLAPSED
    if len(sys.argv) > 1:
        mode = ReasoningMode(sys.argv[1])
    main(mode)
