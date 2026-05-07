"""Offline smoke for chat_pretty.py — drives the renderer with mock events.

Run with:
    uv run python scripts/_smoke_pretty.py

Useful for previewing the rendering before wiring a real LLM.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
for p in (SCRIPTS, SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# Windows legacy consoles default to cp1252; force UTF-8 so we can emit emoji.
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


def envelope(response_type: str, response="", result_map=None) -> dict:
    return {
        "responseType": response_type,
        "response": response,
        "responseAll": "",
        "useTimes": 0,
        "reqId": "smoke",
        "errorMsg": None,
        "resultMap": result_map,
        "conversation_id": "",
        "finished": response_type in {"result", "error", "done"},
    }


SCRIPT = [
    envelope("start", response="开始处理: 调研 src/agent_core 的整体结构"),
    envelope("step", result_map={"turn": 1}),
    envelope("thinking", response="让我分两步:先列目录,再读 README。"),
    envelope("tool_call_start", result_map={
        "tool": "read_file",
        "arguments": {"path": "README.md"},
        "tool_call_id": "call_1",
    }),
    envelope("tool_result", response="# Agent Core ...", result_map={
        "tool": "read_file",
        "toolResult": "# Agent Core Refactor\n\nStandalone scaffold ...",
        "ok": True,
        "elapsed_seconds": 0.04,
        "tool_call_id": "call_1",
    }),
    envelope("step_end", result_map={"turn": 1, "has_tool_calls": True, "elapsed_seconds": 1.2}),
    envelope("step", result_map={"turn": 2}),
    envelope("thinking", response="拿到了 README,可以总结了。"),
    envelope("text", response="这个项目是一个 **Agent Core** 框架,核心是把"),
    envelope("text", response="**循环逻辑**和**业务声明**解耦。它把 ReAct/Pipeline 等"),
    envelope("text", response="循环模式做成可插拔的 Handler。"),
    envelope("step_end", result_map={"turn": 2, "has_tool_calls": False, "elapsed_seconds": 0.8}),
    envelope("usage", result_map={
        "prompt_tokens": 287, "completion_tokens": 64,
        "total_tokens": 351, "total_seconds": 2.0,
    }),
    envelope("result", result_map={
        "taskSummary": "这个项目是一个 **Agent Core** 框架,核心是把循环逻辑和业务声明解耦。",
        "result": "...",
    }),
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
