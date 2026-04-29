"""End-to-end smoke tests through AgentOrchestrationService."""

from __future__ import annotations

from agent_core.base.context import AgentContext
from agent_core.llm.client import LLMResponse
from mock_llm import MockLLMClient
from services.agent_orchestration_service import AgentOrchestrationService


async def test_general_chat_runs():
    service = AgentOrchestrationService()
    llm = MockLLMClient([LLMResponse(content="hello there", finish_reason="stop")])
    context = AgentContext(request_id="t1", query="hi", llm=llm)

    result = await service.run(agent_name="general_chat", query="hi", context=context)
    assert result == "hello there"


async def test_deep_research_registers_planning_tool():
    service = AgentOrchestrationService()
    llm = MockLLMClient([LLMResponse(content="report draft", finish_reason="stop")])
    context = AgentContext(request_id="t2", query="research", llm=llm)

    result = await service.run(agent_name="deep_research", query="research", context=context)
    assert result == "report draft"
    assert context.tool_collection.get("planning_tool") is not None


async def test_file_clerk_adapter_runs_legacy_factory():
    class _LegacyFileClerk:
        def __init__(self, queue):
            self.queue = queue

        async def run(self):
            await self.queue.put({"status": "end", "content": "done"})
            return "legacy finished"

    service = AgentOrchestrationService()
    context = AgentContext(request_id="t3", query="file please")

    result = await service.run(
        agent_name="file_clerk",
        query="file please",
        context=context,
        agent_kwargs={"legacy_factory": lambda queue: _LegacyFileClerk(queue)},
    )

    assert result == "legacy finished"


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
