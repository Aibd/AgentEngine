from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from agentengine.base.agent import AgentRun
from agentengine.base.context import AgentContext
from agentengine.base.state import AgentState
from agentengine.runtime.turn import run_turn
from agentengine.llm.interfaces import LLMChunk, LLMResponse
from agentengine.run_config import RunConfig
from mock_llm import MockLLMClient
from app.backend.services.agent_orchestration_service import AgentOrchestrationService


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


def _make_run(context: AgentContext, *, name: str = "lifecycle_test") -> AgentRun:
    return AgentRun(config=RunConfig(name=name), context=context)


def _make_run_with_teardown(context: AgentContext) -> tuple[AgentRun, dict]:
    counter = {"calls": 0}

    async def teardown(_ctx: AgentContext) -> None:
        counter["calls"] += 1

    spec = RunConfig(name="teardown_test", teardown=teardown)
    return AgentRun(config=spec, context=context), counter


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
    agent = _make_run(context)
    task = asyncio.create_task(run_turn(agent, context, "q"))

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
    agent, counter = _make_run_with_teardown(context)

    result = await run_turn(agent, context, "q")

    assert result == "ok"
    assert agent.state == AgentState.FINISHED
    assert counter["calls"] == 1


async def test_react_teardown_runs_on_cancel():
    context = AgentContext(request_id="cancel-teardown", query="q", llm=_HangingLLM())
    agent, counter = _make_run_with_teardown(context)
    task = asyncio.create_task(run_turn(agent, context, "q"))

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert agent.state == AgentState.CANCELLED
    assert counter["calls"] == 1
