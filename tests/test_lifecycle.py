from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.base.state import AgentState
from agent_core.handlers.pipeline import PipelineHandler
from agent_core.handlers.react import ReActHandler
from agent_core.llm.client import LLMChunk, LLMResponse
from agents.adapters.file_clerk_adapter import FileClerkAdapter
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


async def test_service_close_closes_managed_llm_once():
    llm = _ClosableLLM()
    service = AgentOrchestrationService(llm_factory=lambda: llm)

    result = await service.run(agent_name="general_chat", query="hello")
    await service.close()
    await service.close()

    assert result == "ok"
    assert llm.close_calls == 1


async def test_pipeline_cancellation_marks_agent_cancelled():
    async def wait_forever(context: AgentContext, query: str) -> str:
        await asyncio.Event().wait()
        return query

    context = AgentContext(request_id="cancel-pipeline", query="q")
    agent = BaseAgent(context)
    task = asyncio.create_task(
        PipelineHandler(steps=[wait_forever]).handle(agent, context, "q")
    )

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert agent.state == AgentState.CANCELLED


async def test_react_cancellation_marks_agent_cancelled():
    context = AgentContext(request_id="cancel-react", query="q", llm=_HangingLLM())
    agent = BaseAgent(context)
    task = asyncio.create_task(ReActHandler().handle(agent, context, "q"))

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert agent.state == AgentState.CANCELLED


async def test_file_clerk_cancellation_cancels_legacy_task():
    legacy_instances = []

    class _LegacyFileClerk:
        def __init__(self, queue):
            self.queue = queue
            self.cancelled = False

        async def run(self):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    def factory(queue):
        legacy = _LegacyFileClerk(queue)
        legacy_instances.append(legacy)
        return legacy

    context = AgentContext(request_id="file-cancel", query="q")
    adapter = FileClerkAdapter(context, legacy_factory=factory)
    task = asyncio.create_task(adapter.run_legacy("q"))

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert legacy_instances
    assert legacy_instances[0].cancelled
