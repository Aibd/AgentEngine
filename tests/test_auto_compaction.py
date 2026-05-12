from __future__ import annotations

import pytest

from agentengine.base.agent import AgentRun
from agentengine.base.context import AgentContext
from agentengine.errors import ContextWindowExceededError
from agentengine.llm.client import LLMResponse
from agentengine.memory.message import Message
from agentengine.run_config import RunConfig
from agentengine.runtime.turn import run_turn
from mock_llm import MockLLMClient


class _StaticCompactor:
    def __init__(self) -> None:
        self.calls = 0

    async def compact(self, messages: list[Message]) -> list[Message]:
        self.calls += 1
        return [Message.system("summary"), Message.user("short query")]


class _FailingCompactor:
    async def compact(self, messages: list[Message]) -> list[Message]:
        raise RuntimeError("summary model failed")


async def test_auto_compaction_runs_before_llm_call() -> None:
    compactor = _StaticCompactor()
    llm = MockLLMClient([LLMResponse(content="ok", finish_reason="stop")])
    context = AgentContext(request_id="compact-1", query="q", llm=llm)
    agent = AgentRun(
        config=RunConfig(
            name="compact",
            auto_compact_tokens=20,
            compactor=compactor,
        ),
        context=context,
    )

    result = await run_turn(agent, context, "x" * 200)

    assert result == "ok"
    assert compactor.calls == 1
    assert llm.calls[0]["messages"] == [
        {"role": "system", "content": "summary"},
        {"role": "user", "content": "short query"},
    ]


async def test_auto_compaction_failure_raises_context_error() -> None:
    context = AgentContext(
        request_id="compact-2",
        query="q",
        llm=MockLLMClient([LLMResponse(content="unused", finish_reason="stop")]),
    )
    agent = AgentRun(
        config=RunConfig(
            name="compact",
            auto_compact_tokens=20,
            compactor=_FailingCompactor(),
        ),
        context=context,
    )

    with pytest.raises(ContextWindowExceededError, match="auto-compaction failed"):
        await run_turn(agent, context, "x" * 200)
