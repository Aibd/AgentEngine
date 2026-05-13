from __future__ import annotations

import logging

import httpx

from agentengine.base.agent import AgentRun
from agentengine.base.context import AgentContext
from agentengine.runtime.turn import run_turn
from agentengine.llm.interfaces import LLMResponse
from agentengine.llm.openai_compat import OpenAICompatibleClient
from agentengine.memory.message import Message
from agentengine.run_config import RunConfig
from agentengine.tools.base import Tool
from agentengine.tools.collection import ToolCollection
from mock_llm import MockLLMClient
from examples.services.agent_orchestration_service import AgentOrchestrationService


class _EchoTool(Tool):
    name = "echo"
    description = "Echoes text"
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
    }

    async def run(self, **kwargs):
        return kwargs.get("text", "")


_TOOL_AGENT_SPEC = RunConfig(name="tool_agent")


async def test_react_logs_run_and_tool_without_argument_values(caplog):
    caplog.set_level(logging.INFO)
    llm = MockLLMClient([
        LLMResponse(
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "arguments": '{"text": "secret-value"}',
                    },
                }
            ],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="done", finish_reason="stop"),
    ])
    context = AgentContext(
        request_id="req-log",
        query="run",
        llm=llm,
        tool_collection=ToolCollection([_EchoTool()]),
    )

    agent = AgentRun(config=_TOOL_AGENT_SPEC, context=context)
    result = await run_turn(agent, context, "run")

    assert result == "done"
    assert "agent_run_start request_id=req-log" in caplog.text
    assert "tool_call_start request_id=req-log tool=echo" in caplog.text
    assert "secret-value" not in caplog.text


async def test_service_logs_run_lifecycle(caplog):
    caplog.set_level(logging.INFO)
    llm = MockLLMClient([LLMResponse(content="ok", finish_reason="stop")])
    service = AgentOrchestrationService(llm_factory=lambda: llm)

    result = await service.run(agent_name="general_chat", query="hello")

    assert result == "ok"
    assert "agent_run_start request_id=local agent=general_chat" in caplog.text
    assert "agent_run_finish request_id=local agent=general_chat" in caplog.text


async def test_openai_client_logs_without_api_key(caplog):
    caplog.set_level(logging.DEBUG)
    api_key = "sk-test-secret"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"total_tokens": 1},
            },
            request=request,
        )

    client = OpenAICompatibleClient(
        base_url="https://example.test",
        api_key=api_key,
        model="test-model",
    )
    client._client = httpx.AsyncClient(
        base_url=client.base_url,
        transport=httpx.MockTransport(handler),
    )

    try:
        result = await client.chat([Message.user("hello")], stream=False)
    finally:
        await client.close()

    assert result.content == "ok"
    assert "llm_post_start" in caplog.text
    assert api_key not in caplog.text
