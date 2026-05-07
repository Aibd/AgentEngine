from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.base.state import AgentState
from agent_core.handlers.react import ReActHandler
from agent_core.llm.client import LLMChunk, LLMResponse
from mock_llm import MockLLMClient
from services.agent_orchestration_service import AgentOrchestrationService


class _ClosableLLM(MockLLMClient):
    def __init__(self):
        super().__init__([LLMResponse(content="ok", finish_reason="stop")])
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class _HangingLLM:
    async def chat(self, *args, **kwargs) -> LLMResponse:
        return LLMResponse()

    async def chat_stream(self, *args, **kwargs) -> AsyncIterator[LLMChunk]:
        await asyncio.Event().wait()
        yield LLMChunk()


class _TeardownAgent(BaseAgent):
    def __init__(self, context: AgentContext) -> None:
        super().__init__(context)
        self.teardown_calls = 0

    async def teardown(self) -> None:
        self.teardown_calls += 1


async def test_service_close_closes_managed_llm_once():
    llm = _ClosableLLM()
    service = AgentOrchestrationService(llm_factory=lambda: llm)

    result = await service.run(agent_name="general_chat", query="hello")
    await service.close()
    await service.close()

    assert result == "ok"
    assert llm.close_calls == 1


async def test_react_cancellation_marks_agent_cancelled():
    context = AgentContext(request_id="cancel-react", query="q", llm=_HangingLLM())
    agent = BaseAgent(context)
    task = asyncio.create_task(ReActHandler().handle(agent, context, "q"))

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert agent.state == AgentState.CANCELLED


async def test_react_teardown_runs_on_success():
    context = AgentContext(
        request_id="teardown-react",
        query="q",
        llm=MockLLMClient([LLMResponse(content="ok", finish_reason="stop")]),
    )
    agent = _TeardownAgent(context)

    result = await ReActHandler().handle(agent, context, "q")

    assert result == "ok"
    assert agent.state == AgentState.FINISHED
    assert agent.teardown_calls == 1


async def test_react_teardown_runs_on_cancel():
    context = AgentContext(request_id="cancel-teardown", query="q", llm=_HangingLLM())
    agent = _TeardownAgent(context)
    task = asyncio.create_task(ReActHandler().handle(agent, context, "q"))

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert agent.state == AgentState.CANCELLED
    assert agent.teardown_calls == 1


