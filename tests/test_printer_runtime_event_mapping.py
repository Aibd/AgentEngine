from __future__ import annotations

import pytest

from agent_core.runtime.events import (
    RunCompleted,
    RunFailed,
    RunStarted,
    TextDelta,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
    ToolStreamEventEmitted,
)
from agent_core.stream.printer import Printer
from agent_core.stream.sse_queue import SseEventQueue


@pytest.mark.parametrize(
    ("event", "event_type"),
    [
        (RunStarted(run_id="run_1", turn_id="turn_1", input_summary="hello"), "start"),
        (TextDelta(run_id="run_1", turn_id="turn_1", content="hi"), "text"),
        (
            ToolCallStarted(
                run_id="run_1",
                turn_id="turn_1",
                tool_call_id="tc_1",
                tool_name="echo",
            ),
            "tool_call_start",
        ),
        (
            ToolCallCompleted(
                run_id="run_1",
                turn_id="turn_1",
                tool_call_id="tc_1",
                tool_name="echo",
                result_summary="done",
            ),
            "tool_result",
        ),
        (
            ToolStreamEventEmitted(
                run_id="run_1",
                turn_id="turn_1",
                tool_call_id="tc_1",
                tool_name="search",
                stream_event_type="search_result",
                data={"title": "match"},
            ),
            "search_result",
        ),
        (
            ToolCallFailed(
                run_id="run_1",
                turn_id="turn_1",
                tool_call_id="tc_1",
                tool_name="echo",
                error_message="boom",
            ),
            "tool_result",
        ),
        (RunCompleted(run_id="run_1", turn_id="turn_1", result_summary="ok"), "done"),
        (
            RunFailed(
                run_id="run_1",
                turn_id="turn_1",
                error_type="RuntimeError",
                error_message="boom",
            ),
            "error",
        ),
    ],
)
async def test_printer_maps_runtime_event_to_sse(event_type: str, event) -> None:
    stream = SseEventQueue()
    printer = Printer("req-1", stream, conversation_id="conv-1")

    await printer.from_runtime_event(event)
    sse = await stream._queue.get()
    if "comment" in sse:
        assert "run_id=run_1" in sse["comment"]
        sse = await stream._queue.get()

    assert sse["event"] == event_type
    assert sse["data"]["request_id"] == "req-1"
    assert sse["data"]["conversation_id"] == "conv-1"
