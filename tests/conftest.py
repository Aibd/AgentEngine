from __future__ import annotations

import pytest

from agentkit.base.context import AgentContext
from agentkit.llm.client import LLMResponse
from mock_llm import MockLLMClient
from agentkit.stream.printer import Printer
from agentkit.stream.sse_queue import SseEventQueue
from agentkit.tools.base import Tool
from agentkit.tools.collection import ToolCollection


class _EchoTool(Tool):
    name = "echo"
    description = "Echoes input back"
    schema = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def run(self, **kwargs):
        return kwargs.get("text", "echo")


@pytest.fixture
def event_stream() -> SseEventQueue:
    return SseEventQueue()


@pytest.fixture
def printer(event_stream: SseEventQueue) -> Printer:
    return Printer(request_id="test-001", event_stream=event_stream)


@pytest.fixture
def tool_collection() -> ToolCollection:
    coll = ToolCollection()
    coll.add(_EchoTool())
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
                    "name": "read_file",
                    "arguments": '{"path": "README.md"}',
                },
            }
        ],
    )
