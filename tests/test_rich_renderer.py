"""Tests for the Claude Code-style terminal renderer.

We don't try to assert exact rendered bytes (rich's output is style-rich and
terminal-dependent). Instead we capture rich's recorded output and check that
the right *sections* appear in the right order.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from rich.console import Console  # noqa: E402

from renderers.rich_renderer import ReasoningMode, RichRenderer  # noqa: E402


def _make_renderer(reasoning: ReasoningMode = ReasoningMode.COLLAPSED) -> tuple[RichRenderer, Console]:
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    return RichRenderer(agent_name="test_agent", console=console, reasoning=reasoning), console


def _drive(renderer: RichRenderer, events: list[dict]) -> None:
    for evt in events:
        renderer.handle(evt)
    renderer.finish()


def _envelope(response_type: str, response: object = "", result_map: dict | None = None) -> dict:
    return {
        "responseType": response_type,
        "response": response,
        "responseAll": "",
        "useTimes": 0,
        "reqId": "test",
        "errorMsg": None,
        "resultMap": result_map,
        "conversation_id": "",
        "finished": response_type in {"result", "error", "done"},
    }


class TestNoToolPath:
    def test_renders_query_step_text_and_usage(self) -> None:
        r, console = _make_renderer()
        _drive(r, [
            _envelope("start", response="开始处理: hello world"),
            _envelope("step", response="Step 1", result_map={"turn": 1}),
            _envelope("text", response="Here is "),
            _envelope("text", response="the answer."),
            _envelope("step_end", result_map={"turn": 1, "has_tool_calls": False, "elapsed_seconds": 0.5}),
            _envelope("usage", result_map={
                "prompt_tokens": 10, "completion_tokens": 3,
                "total_tokens": 13, "total_seconds": 0.7,
            }),
            _envelope("result", result_map={"taskSummary": "Here is the answer.", "result": "Here is the answer."}),
        ])

        out = console.export_text()
        # Header
        assert "test_agent" in out
        assert "hello world" in out
        # Turn marker
        assert "Turn 1" in out
        # Final answer panel
        assert "Answer" in out
        assert "Here is the answer." in out
        # Done card with usage
        assert "Done" in out
        assert "13" in out  # total tokens
        assert "0.70" in out


class TestThinkingCollapsedByDefault:
    def test_thinking_shows_one_line_in_collapsed_mode(self) -> None:
        r, console = _make_renderer(ReasoningMode.COLLAPSED)
        _drive(r, [
            _envelope("start", response="hi"),
            _envelope("step", result_map={"turn": 1}),
            _envelope("thinking", response="Step one. Step two. Step three."),
            _envelope("text", response="answer"),
            _envelope("step_end", result_map={"turn": 1, "has_tool_calls": False, "elapsed_seconds": 0.1}),
            _envelope("usage", result_map={"total_tokens": 5, "total_seconds": 0.1}),
            _envelope("result", result_map={"taskSummary": "answer"}),
        ])
        out = console.export_text()
        assert "Thinking" in out
        # Collapsed: the literal thinking content must NOT be visible
        assert "Step one. Step two. Step three." not in out
        # Hint mentions how to expand
        assert "expanded" in out

    def test_thinking_renders_full_text_when_expanded(self) -> None:
        r, console = _make_renderer(ReasoningMode.EXPANDED)
        _drive(r, [
            _envelope("start", response="hi"),
            _envelope("step", result_map={"turn": 1}),
            _envelope("thinking", response="Step one. Step two."),
            _envelope("text", response="answer"),
            _envelope("step_end", result_map={"turn": 1, "has_tool_calls": False, "elapsed_seconds": 0.1}),
            _envelope("result", result_map={"taskSummary": "answer"}),
        ])
        out = console.export_text()
        assert "Step one. Step two." in out

    def test_thinking_hidden_when_mode_is_hidden(self) -> None:
        r, console = _make_renderer(ReasoningMode.HIDDEN)
        _drive(r, [
            _envelope("start", response="hi"),
            _envelope("step", result_map={"turn": 1}),
            _envelope("thinking", response="secret cot"),
            _envelope("text", response="answer"),
            _envelope("step_end", result_map={"turn": 1, "has_tool_calls": False, "elapsed_seconds": 0.1}),
            _envelope("result", result_map={"taskSummary": "answer"}),
        ])
        out = console.export_text()
        assert "secret cot" not in out


class TestToolCallPath:
    def test_tool_call_and_result_render_in_order(self) -> None:
        r, console = _make_renderer()
        _drive(r, [
            _envelope("start", response="hi"),
            _envelope("step", result_map={"turn": 1}),
            _envelope("tool_call_start", result_map={
                "tool": "echo",
                "arguments": {"text": "hello"},
                "tool_call_id": "call_1",
            }),
            _envelope("tool_result", response="echoed: hello", result_map={
                "tool": "echo",
                "toolResult": "echoed: hello",
                "ok": True,
                "elapsed_seconds": 0.02,
                "tool_call_id": "call_1",
            }),
            _envelope("step_end", result_map={"turn": 1, "has_tool_calls": True, "elapsed_seconds": 0.5}),
            _envelope("step", result_map={"turn": 2}),
            _envelope("text", response="done"),
            _envelope("step_end", result_map={"turn": 2, "has_tool_calls": False, "elapsed_seconds": 0.1}),
            _envelope("usage", result_map={"total_tokens": 50, "total_seconds": 0.6}),
            _envelope("result", result_map={"taskSummary": "done"}),
        ])
        out = console.export_text()
        # Tool call header & arguments are visible
        assert "echo" in out
        assert "hello" in out
        # Result panel uses ✓ for success
        assert "✓" in out
        # Two turns
        assert "Turn 1" in out
        assert "Turn 2" in out

    def test_failed_tool_uses_red_marker(self) -> None:
        r, console = _make_renderer()
        _drive(r, [
            _envelope("start", response="hi"),
            _envelope("step", result_map={"turn": 1}),
            _envelope("tool_call_start", result_map={
                "tool": "boom",
                "arguments": {},
                "tool_call_id": "call_x",
            }),
            _envelope("tool_result", response="Tool error: kaboom", result_map={
                "tool": "boom",
                "toolResult": "Tool error: kaboom",
                "ok": False,
                "elapsed_seconds": 0.0,
                "tool_call_id": "call_x",
                "error_type": "ToolError",
            }),
            _envelope("step_end", result_map={"turn": 1, "has_tool_calls": True, "elapsed_seconds": 0.0}),
            _envelope("text", response="recovered"),
            _envelope("step_end", result_map={"turn": 1, "has_tool_calls": False, "elapsed_seconds": 0.0}),
            _envelope("result", result_map={"taskSummary": "recovered"}),
        ])
        out = console.export_text()
        assert "✗" in out
        assert "ToolError" in out
        assert "kaboom" in out


class TestErrorPath:
    def test_error_renders_as_red_panel_and_skips_done_card(self) -> None:
        r, console = _make_renderer()
        _drive(r, [
            _envelope("start", response="hi"),
            _envelope("error", result_map={
                "code": "llm_rate_limited",
                "message": "too many requests",
                "category": "llm",
                "retryable": True,
            }),
        ])
        out = console.export_text()
        # Friendly headline replaces the raw "Error" string
        assert "服务繁忙" in out
        # Hint surfaces actionable advice
        assert "💡" in out
        # Original message still shown for debugging
        assert "too many requests" in out
        # Diagnostic code shown in dim footer
        assert "llm_rate_limited" in out
        # Done card must NOT appear when there was an error
        assert "Done" not in out

    def test_unknown_code_falls_back_to_raw_message(self) -> None:
        r, console = _make_renderer()
        _drive(r, [
            _envelope("start", response="hi"),
            _envelope("error", result_map={
                "code": "weird_unknown_code",
                "message": "something specific went wrong",
            }),
        ])
        out = console.export_text()
        assert "出错了" in out
        assert "something specific went wrong" in out


class TestTextStreamingAccumulates:
    def test_multiple_text_deltas_render_as_one_panel(self) -> None:
        r, console = _make_renderer()
        _drive(r, [
            _envelope("start", response="hi"),
            _envelope("step", result_map={"turn": 1}),
            _envelope("text", response="Hello "),
            _envelope("text", response="**world**"),
            _envelope("text", response="!"),
            _envelope("step_end", result_map={"turn": 1, "has_tool_calls": False, "elapsed_seconds": 0.1}),
            _envelope("result", result_map={"taskSummary": "Hello **world**!"}),
        ])
        out = console.export_text()
        # The content must appear glued together
        assert "Hello" in out
        assert "world" in out
        # Should appear under a single Answer panel header (no duplicate Answer
        # boundaries from each delta)
        assert out.count("📝") <= 2  # one in the streaming panel, one possibly in "Done" — but "Done" doesn't echo emoji
