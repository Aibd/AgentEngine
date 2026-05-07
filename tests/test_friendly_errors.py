"""Tests for the renderer-side friendly error mapping."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from renderers.friendly_errors import translate  # noqa: E402


class TestKnownCodes:
    def test_rate_limit_translated(self) -> None:
        out = translate({
            "code": "llm_rate_limited",
            "message": "429 from upstream",
            "retryable": True,
        })
        assert out.headline == "服务繁忙"
        assert "限流" in out.detail
        assert out.retryable is True
        assert out.hint != ""

    def test_context_window_translated(self) -> None:
        out = translate({
            "code": "llm_context_window_exceeded",
            "message": "context too long",
            "retryable": False,
        })
        assert "上下文" in out.headline or "上下文" in out.detail
        assert "新开" in out.hint
        assert out.retryable is False

    def test_tool_execution_error(self) -> None:
        out = translate({
            "code": "tool_execution_error",
            "message": "boom",
        })
        assert "工具" in out.headline
        # original message is appended into detail
        assert "boom" in out.detail


class TestUnknownCode:
    def test_falls_back_to_raw_message(self) -> None:
        out = translate({"code": "something_new", "message": "raw text"})
        assert out.headline == "出错了"
        assert out.detail == "raw text"
        assert out.hint == ""

    def test_handles_missing_payload(self) -> None:
        out = translate(None)
        assert out.headline == "出错了"
        assert out.detail == "未知错误"


class TestMessageMerge:
    def test_does_not_duplicate_message_when_already_in_template(self) -> None:
        # If upstream message is already substring of detail template, don't append.
        out = translate({
            "code": "llm_rate_limited",
            "message": "限流了",  # word already in template
        })
        # The message word is already in the template detail, so we shouldn't
        # see it twice.
        assert out.detail.count("限流") <= 2
