from __future__ import annotations

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.base.state import AgentState
from agent_core.handlers.legacy import LegacyHandler
from agent_core.handlers.pipeline import PipelineHandler
from agent_core.memory.message import Role


async def test_pipeline_handler_runs_multiple_steps_in_order():
    async def add_a(context: AgentContext, value: str) -> str:
        return f"{value}-a"

    async def add_b(context: AgentContext, value: str) -> str:
        return f"{value}-b"

    context = AgentContext(request_id="pipeline", query="start")
    agent = BaseAgent(context)

    result = await PipelineHandler(steps=[add_a, add_b]).handle(agent, context, "start")

    assert result == "start-a-b"
    assert agent.state == AgentState.FINISHED
    assert agent.current_step == 2
    assert agent.memory.messages[0].role == Role.USER


async def test_legacy_handler_runs_sync_legacy_agent():
    class _SyncLegacy:
        def run(self, query: str) -> str:
            return f"sync:{query}"

    context = AgentContext(request_id="legacy-sync", query="hello")
    agent = BaseAgent(context)

    result = await LegacyHandler(factory=lambda _: _SyncLegacy()).handle(
        agent,
        context,
        "hello",
    )

    assert result == "sync:hello"
    assert agent.state == AgentState.FINISHED


async def test_legacy_handler_runs_async_legacy_agent():
    class _AsyncLegacy:
        async def run(self, query: str) -> str:
            return f"async:{query}"

    context = AgentContext(request_id="legacy-async", query="hello")
    agent = BaseAgent(context)

    result = await LegacyHandler(factory=lambda _: _AsyncLegacy()).handle(
        agent,
        context,
        "hello",
    )

    assert result == "async:hello"
    assert agent.state == AgentState.FINISHED
