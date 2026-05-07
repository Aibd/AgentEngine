"""Tests for the Claude Code-style terminal renderer."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from rich.console import Console  # noqa: E402

from renderers.rich_renderer import ReasoningMode, RichRenderer  # noqa: E402


def _make_renderer(
    reasoning: ReasoningMode = ReasoningMode.COLLAPSED,
) -> tuple[RichRenderer, Console]:
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    return RichRenderer(agent_name="test_agent", console=console, reasoning=reasoning), console


def _drive(renderer: RichRenderer, events: list[dict]) -> None:
    for evt in events:
        renderer.handle(evt)
    renderer.finish()


def _frame(event: str, data: dict | None = None) -> dict:
    return {"event": event, "data": data or {}}


class TestNoToolPath:
    def test_renders_query_step_text_and_usage(self) -> None:
        r, console = _make_renderer()
        _drive(
            r,
            [
                _frame("start", {"query": "hello world"}),
                _frame("step", {"turn": 1}),
                _frame("text", {"delta": "Here is "}),
                _frame("text", {"delta": "the answer."}),
                _frame("step_end", {"turn": 1, "has_tool_calls": False, "elapsed_ms": 500}),
                _frame(
                    "usage",
                    {
                        "prompt_tokens": 10,
                        "completion_tokens": 3,
                        "total_tokens": 13,
                        "total_seconds": 0.7,
                    },
                ),
                _frame("done", {"reason": "completed", "result": "Here is the answer."}),
            ],
        )

        out = console.export_text()
        assert "test_agent" in out
        assert "hello world" in out
        assert "Turn 1" in out
        assert "Answer" in out
        assert "Here is the answer." in out
        assert "Done" in out
        assert "13" in out
        assert "0.70" in out


class TestThinkingModes:
    def test_thinking_shows_one_line_in_collapsed_mode(self) -> None:
        r, console = _make_renderer(ReasoningMode.COLLAPSED)
        _drive(
            r,
            [
                _frame("start", {"query": "hi"}),
                _frame("step", {"turn": 1}),
                _frame("thinking", {"delta": "Step one. Step two. Step three."}),
                _frame("text", {"delta": "answer"}),
                _frame("step_end", {"turn": 1, "has_tool_calls": False, "elapsed_ms": 100}),
                _frame("usage", {"total_tokens": 5, "total_seconds": 0.1}),
                _frame("done", {"result": "answer"}),
            ],
        )
        out = console.export_text()
        assert "Thinking" in out
        assert "Step one. Step two. Step three." not in out
        assert "expanded" in out

    def test_thinking_renders_full_text_when_expanded(self) -> None:
        r, console = _make_renderer(ReasoningMode.EXPANDED)
        _drive(
            r,
            [
                _frame("start", {"query": "hi"}),
                _frame("step", {"turn": 1}),
                _frame("thinking", {"delta": "Step one. Step two."}),
                _frame("text", {"delta": "answer"}),
                _frame("step_end", {"turn": 1, "has_tool_calls": False, "elapsed_ms": 100}),
                _frame("done", {"result": "answer"}),
            ],
        )
        assert "Step one. Step two." in console.export_text()

    def test_thinking_hidden_when_mode_is_hidden(self) -> None:
        r, console = _make_renderer(ReasoningMode.HIDDEN)
        _drive(
            r,
            [
                _frame("start", {"query": "hi"}),
                _frame("step", {"turn": 1}),
                _frame("thinking", {"delta": "secret cot"}),
                _frame("text", {"delta": "answer"}),
                _frame("step_end", {"turn": 1, "has_tool_calls": False, "elapsed_ms": 100}),
                _frame("done", {"result": "answer"}),
            ],
        )
        assert "secret cot" not in console.export_text()


class TestToolCallPath:
    def test_tool_call_and_result_render_in_order(self) -> None:
        r, console = _make_renderer()
        _drive(
            r,
            [
                _frame("start", {"query": "hi"}),
                _frame("step", {"turn": 1}),
                _frame(
                    "tool_call_start",
                    {
                        "tool": "echo",
                        "arguments": {"text": "hello"},
                        "tool_call_id": "call_1",
                    },
                ),
                _frame(
                    "tool_result",
                    {
                        "tool": "echo",
                        "result": "echoed: hello",
                        "ok": True,
                        "elapsed_ms": 20,
                        "tool_call_id": "call_1",
                    },
                ),
                _frame("step_end", {"turn": 1, "has_tool_calls": True, "elapsed_ms": 500}),
                _frame("step", {"turn": 2}),
                _frame("text", {"delta": "done"}),
                _frame("step_end", {"turn": 2, "has_tool_calls": False, "elapsed_ms": 100}),
                _frame("usage", {"total_tokens": 50, "total_seconds": 0.6}),
                _frame("done", {"result": "done"}),
            ],
        )
        out = console.export_text()
        assert "echo" in out
        assert "hello" in out
        assert "Result" in out
        assert "Turn 1" in out
        assert "Turn 2" in out

    def test_failed_tool_uses_red_marker(self) -> None:
        r, console = _make_renderer()
        _drive(
            r,
            [
                _frame("start", {"query": "hi"}),
                _frame("step", {"turn": 1}),
                _frame("tool_call_start", {"tool": "boom", "arguments": {}, "tool_call_id": "call_x"}),
                _frame(
                    "tool_result",
                    {
                        "tool": "boom",
                        "result": "Tool error: kaboom",
                        "ok": False,
                        "elapsed_ms": 0,
                        "tool_call_id": "call_x",
                        "error_type": "ToolError",
                    },
                ),
                _frame("step_end", {"turn": 1, "has_tool_calls": True, "elapsed_ms": 0}),
                _frame("text", {"delta": "recovered"}),
                _frame("step_end", {"turn": 1, "has_tool_calls": False, "elapsed_ms": 0}),
                _frame("done", {"result": "recovered"}),
            ],
        )
        out = console.export_text()
        assert "ToolError" in out
        assert "kaboom" in out


class TestErrorPath:
    def test_error_renders_as_red_panel_and_skips_done_card(self) -> None:
        r, console = _make_renderer()
        _drive(
            r,
            [
                _frame("start", {"query": "hi"}),
                _frame(
                    "error",
                    {
                        "code": "llm_rate_limited",
                        "message": "too many requests",
                        "category": "llm",
                        "retryable": True,
                    },
                ),
            ],
        )
        out = console.export_text()
        assert "too many requests" in out
        assert "llm_rate_limited" in out
        assert "Done" not in out

    def test_unknown_code_falls_back_to_raw_message(self) -> None:
        r, console = _make_renderer()
        _drive(
            r,
            [
                _frame("start", {"query": "hi"}),
                _frame("error", {"code": "weird_unknown_code", "message": "something specific went wrong"}),
            ],
        )
        out = console.export_text()
        assert "something specific went wrong" in out


class TestTextStreamingAccumulates:
    def test_multiple_text_deltas_render_as_one_panel(self) -> None:
        r, console = _make_renderer()
        _drive(
            r,
            [
                _frame("start", {"query": "hi"}),
                _frame("step", {"turn": 1}),
                _frame("text", {"delta": "Hello "}),
                _frame("text", {"delta": "**world**"}),
                _frame("text", {"delta": "!"}),
                _frame("step_end", {"turn": 1, "has_tool_calls": False, "elapsed_ms": 100}),
                _frame("done", {"result": "Hello **world**!"}),
            ],
        )
        out = console.export_text()
        assert "Hello" in out
        assert "world" in out
        assert out.count("??") <= 2
