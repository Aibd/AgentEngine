from __future__ import annotations

import pytest

from agentkit.runtime.events import (
    RunCompleted,
    RunFailed,
    RunStarted,
    TextDelta,
    TodosUpdated,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
    ToolStreamEventEmitted,
    UserQuestionAsked,
)
from agentkit.stream.printer import Printer
from agentkit.stream.sse_queue import SseEventQueue


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
        (
            TodosUpdated(
                run_id="run_1",
                turn_id="turn_1",
                todos=[{"content": "x", "activeForm": "Xing", "status": "in_progress"}],
            ),
            "todos_updated",
        ),
        (
            UserQuestionAsked(
                run_id="run_1",
                turn_id="turn_1",
                question_id="q_1",
                question="Which?",
                options=["a", "b"],
                multiple=False,
            ),
            "user_question_asked",
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


async def test_printer_preserves_todos_payload() -> None:
    stream = SseEventQueue()
    printer = Printer("req-1", stream, conversation_id="conv-1")
    todos = [
        {"content": "Build", "activeForm": "Building", "status": "in_progress"},
        {"content": "Ship", "activeForm": "Shipping", "status": "pending"},
    ]
    await printer.from_runtime_event(
        TodosUpdated(run_id="r", turn_id="t", todos=todos)
    )
    sse = await stream._queue.get()
    assert sse["event"] == "todos_updated"
    assert sse["data"]["todos"] == todos
    assert sse["data"]["run_id"] == "r"
    assert sse["data"]["turn_id"] == "t"


async def test_printer_preserves_user_question_payload() -> None:
    stream = SseEventQueue()
    printer = Printer("req-1", stream, conversation_id="conv-1")
    await printer.from_runtime_event(
        UserQuestionAsked(
            run_id="r",
            turn_id="t",
            question_id="q_42",
            question="Pick a backend",
            options=["pg", "mysql"],
            multiple=True,
        )
    )
    sse = await stream._queue.get()
    assert sse["event"] == "user_question_asked"
    assert sse["data"]["question_id"] == "q_42"
    assert sse["data"]["question"] == "Pick a backend"
    assert sse["data"]["options"] == ["pg", "mysql"]
    assert sse["data"]["multiple"] is True
