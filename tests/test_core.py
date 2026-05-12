"""End-to-end smoke tests through AgentOrchestrationService."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from agentengine import AgentEngine, AgentPreset, DEFAULT_AGENT_SYSTEM_PROMPT
from agentengine.base.context import AgentContext
from agentengine.llm.client import LLMChunk, LLMResponse
from agentengine.runtime.events import RunCancelled
from mock_llm import MockLLMClient
from examples.reference_app.services.agent_orchestration_service import AgentOrchestrationService


class _HangingLLM:
    async def chat(self, *args, **kwargs) -> LLMResponse:
        return LLMResponse()

    async def chat_stream(self, *args, **kwargs) -> AsyncIterator[LLMChunk]:
        await asyncio.Event().wait()
        yield LLMChunk()


async def test_general_chat_runs():
    service = AgentOrchestrationService()
    llm = MockLLMClient([LLMResponse(content="hello there", finish_reason="stop")])
    context = AgentContext(request_id="t1", query="hi", llm=llm)

    result = await service.run(agent_name="general_chat", query="hi", context=context)
    assert result == "hello there"


async def test_deep_research_agent_runs():
    service = AgentOrchestrationService()
    llm = MockLLMClient([LLMResponse(content="report draft", finish_reason="stop")])
    context = AgentContext(request_id="t2", query="research", llm=llm)

    result = await service.run(agent_name="deep_research", query="research", context=context)
    assert result == "report draft"


async def test_streaming_context_factory():
    service = AgentOrchestrationService()
    context, stream = service.create_streaming_context(
        request_id="r1",
        query="q",
        conversation_id="c1",
    )
    assert context.printer is not None
    assert context.conversation_id == "c1"
    assert stream is not None


async def test_service_uses_llm_factory_when_context_has_no_llm():
    llm = MockLLMClient([LLMResponse(content="factory response", finish_reason="stop")])
    service = AgentOrchestrationService(llm_factory=lambda: llm)

    result = await service.run(agent_name="general_chat", query="hello")

    assert result == "factory response"
    assert len(llm.calls) == 1


async def test_service_rejects_empty_query():
    service = AgentOrchestrationService()

    with pytest.raises(ValueError, match="query"):
        await service.run(agent_name="general_chat", query="   ")


async def test_service_rejects_too_long_query():
    service = AgentOrchestrationService(max_query_chars=4)

    with pytest.raises(ValueError, match="too long"):
        await service.run(agent_name="general_chat", query="hello")


async def test_public_engine_uses_explicit_presets_and_llm():
    llm = MockLLMClient([LLMResponse(content="sdk response", finish_reason="stop")])
    engine = AgentEngine(
        presets={"chat": AgentPreset(name="chat", instructions="Be brief.")},
    )
    context = AgentContext(request_id="sdk-1", query="hello", llm=llm)

    result = await engine.run(agent_name="chat", query="hello", context=context)

    assert result == "sdk response"
    assert llm.calls[0]["messages"][0]["role"] == "system"
    assert DEFAULT_AGENT_SYSTEM_PROMPT in llm.calls[0]["messages"][0]["content"]


async def test_public_engine_requires_explicit_llm():
    engine = AgentEngine(presets={"chat": AgentPreset(name="chat")})

    with pytest.raises(RuntimeError, match="No LLM client configured"):
        await engine.run(agent_name="chat", query="hello")


async def test_public_engine_interrupt_cancels_active_run():
    engine = AgentEngine(presets={"chat": AgentPreset(name="chat")})
    context = AgentContext(request_id="interrupt-me", query="hello", llm=_HangingLLM())

    task = asyncio.create_task(
        engine.run(agent_name="chat", query="hello", context=context)
    )
    await asyncio.sleep(0)

    assert engine.interrupt("interrupt-me", "user stopped") is True
    with pytest.raises(asyncio.CancelledError):
        await task

    assert context.extras["agent_state"] == "cancelled"
    assert any(isinstance(event, RunCancelled) for event in context.extras["runtime_events"])
    assert engine.interrupt("interrupt-me") is False
