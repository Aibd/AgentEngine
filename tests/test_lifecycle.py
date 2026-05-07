from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from agent_core.base.agent import AgentRun
from agent_core.base.context import AgentContext
from agent_core.base.state import AgentState
from agent_core.runtime.turn import run_turn
from agent_core.llm.client import LLMChunk, LLMResponse
from agent_core.spec import AgentSpec
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


def _make_run(context: AgentContext, *, name: str = "lifecycle_test", max_steps: int = 10) -> AgentRun:
    return AgentRun(spec=AgentSpec(name=name, max_steps=max_steps), context=context)


def _make_run_with_teardown(context: AgentContext) -> tuple[AgentRun, dict]:
    counter = {"calls": 0}

    async def teardown(_ctx: AgentContext) -> None:
        counter["calls"] += 1

    spec = AgentSpec(name="teardown_test", teardown=teardown)
    return AgentRun(spec=spec, context=context), counter


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
