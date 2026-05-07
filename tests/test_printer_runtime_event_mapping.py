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
)
from agent_core.stream.event_stream import EventStream
from agent_core.stream.printer import Printer


@pytest.mark.parametrize(
    ("event", "response_type"),
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
            ToolCallFailed(
                run_id="run_1",
                turn_id="turn_1",
                tool_call_id="tc_1",
                tool_name="echo",
                error_message="boom",
            ),
            "tool_result",
        ),
        (RunCompleted(run_id="run_1", turn_id="turn_1", result_summary="ok"), "result"),
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
async def test_printer_maps_runtime_event_to_sse(response_type: str, event) -> None:
    stream = EventStream()
    printer = Printer("req-1", stream, conversation_id="conv-1")

    await printer.from_runtime_event(event)
    sse = await stream._queue.get()

    assert sse["responseType"] == response_type
    assert sse["reqId"] == "req-1"
    assert sse["conversation_id"] == "conv-1"
