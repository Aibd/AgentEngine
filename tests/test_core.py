import pytest

from agent_core.base.context import AgentContext
from agent_core.llm.client import LLMResponse
from agent_core.memory.message import Message
from services.agent_orchestration_service import AgentOrchestrationService


class FakeLLM:
    async def chat(self, messages: list[Message], *, tools=None, stream=False, **kwargs):
        return LLMResponse(content="ok")

    async def chat_stream(self, messages: list[Message], *, tools=None, **kwargs):
        yield LLMResponse(content="ok")


@pytest.mark.asyncio
async def test_general_chat_runs_with_fake_llm():
    service = AgentOrchestrationService()
    context = AgentContext(request_id="test", query="hello", llm=FakeLLM())
    result = await service.run(agent_name="general_chat", query="hello", context=context)
    assert result == "ok"


@pytest.mark.asyncio
async def test_deep_research_registers_planning_tool():
    service = AgentOrchestrationService()
    context = AgentContext(request_id="test", query="research", llm=FakeLLM())
    result = await service.run(agent_name="deep_research", query="research", context=context)
    assert result == "ok"
    assert context.tool_collection.get("planning_tool") is not None
