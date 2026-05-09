from __future__ import annotations

from typing import AsyncGenerator

from agentengine.runtime.events import (
    RuntimeEvent,
    ToolCallCompleted,
    ToolCallStarted,
    ToolStreamEventEmitted,
)
from agentengine.tools.base import StreamingTool, ToolStreamEvent
from agentengine.tools.executor import ToolExecutor


class _StreamingSearchTool(StreamingTool):
    name = "streaming_search"

    async def run_stream(
        self, **kwargs
    ) -> AsyncGenerator[ToolStreamEvent, None]:
        yield ToolStreamEvent(
            event_type="search_result",
            data={"title": kwargs.get("query", "")},
        )
        yield ToolStreamEvent(event_type="result", data="final answer", is_final=True)


async def test_streaming_tool_intermediate_events_are_runtime_events() -> None:
    events: list[RuntimeEvent] = []

    async def on_event(event: RuntimeEvent) -> None:
        events.append(event)

    result = await ToolExecutor(
        run_id="run_1",
        turn_id="turn_1",
        on_event=on_event,
    ).execute(_StreamingSearchTool(), {"query": "phase 4"}, tool_call_id="tc_1")

    assert result.ok is True
    assert result.content == "final answer"
    assert [type(event) for event in events] == [
        ToolCallStarted,
        ToolStreamEventEmitted,
        ToolCallCompleted,
    ]

    stream_event = events[1]
    assert isinstance(stream_event, ToolStreamEventEmitted)
    assert stream_event.stream_event_type == "search_result"
    assert stream_event.data == {"title": "phase 4"}
