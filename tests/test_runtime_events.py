from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentengine.runtime.events import (
    RunStarted,
    RuntimeEvent,
    TextDelta,
    ToolCallStarted,
    ToolStreamEventEmitted,
)
from agentengine.stream.events import EventType


def test_runtime_event_serializes_common_fields() -> None:
    event = RunStarted(
        run_id="run_1",
        turn_id="turn_1",
        agent_name="general_chat",
        input_summary="hello",
    )

    data = event.to_dict()

    assert data["event_type"] == "run_started"
    assert data["run_id"] == "run_1"
    assert data["turn_id"] == "turn_1"
    assert data["agent_name"] == "general_chat"
    assert isinstance(data["timestamp"], str)


def test_runtime_event_is_immutable() -> None:
    event = TextDelta(run_id="run_1", turn_id="turn_1", content="hi")

    with pytest.raises(FrozenInstanceError):
        event.content = "bye"  # type: ignore[misc]


def test_runtime_event_layer_is_distinct_from_sse_event_type() -> None:
    event = ToolCallStarted(
        run_id="run_1",
        turn_id="turn_1",
        tool_call_id="tc_1",
        tool_name="echo",
        arguments={"text": "hello"},
    )

    assert isinstance(event, RuntimeEvent)
    assert event.event_type == "tool_call_started"
    assert EventType.TOOL_RESULT.value == "tool_result"
    assert event.event_type != EventType.TOOL_RESULT.value


def test_streaming_tool_event_stays_runtime_semantic() -> None:
    event = ToolStreamEventEmitted(
        run_id="run_1",
        turn_id="turn_1",
        tool_call_id="tc_1",
        tool_name="search",
        stream_event_type=EventType.SEARCH_RESULT.value,
        data={"title": "match"},
    )

    assert event.event_type == "tool_stream_event"
    assert event.stream_event_type == "search_result"
    assert event.to_dict()["data"] == {"title": "match"}
