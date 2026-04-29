from __future__ import annotations

import pytest

from agent_core.base.context import AgentContext
from agent_core.llm.client import LLMResponse
from mock_llm import MockLLMClient
from agent_core.stream.event_stream import EventStream
from agent_core.stream.printer import Printer
from agent_core.tools.builtin.planning_tool import PlanningTool
from agent_core.tools.collection import ToolCollection


@pytest.fixture
def event_stream() -> EventStream:
    return EventStream()


@pytest.fixture
def printer(event_stream: EventStream) -> Printer:
    return Printer(request_id="test-001", event_stream=event_stream)


@pytest.fixture
def tool_collection() -> ToolCollection:
    coll = ToolCollection()
    coll.add(PlanningTool())
    return coll


@pytest.fixture
def mock_llm() -> MockLLMClient:
    return MockLLMClient()


@pytest.fixture
def agent_context(printer: Printer, tool_collection: ToolCollection, mock_llm: MockLLMClient) -> AgentContext:
    return AgentContext(
        request_id="test-001",
        query="test query",
        llm=mock_llm,
        printer=printer,
        tool_collection=tool_collection,
    )


@pytest.fixture
def assistant_response() -> LLMResponse:
    return LLMResponse(content="I found the answer.", finish_reason="stop")


@pytest.fixture
def tool_call_response() -> LLMResponse:
    return LLMResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "planning_tool",
                    "arguments": '{"action": "create", "steps": ["step 1", "step 2"]}',
                },
            }
        ],
    )
