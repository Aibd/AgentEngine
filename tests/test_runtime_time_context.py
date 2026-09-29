"""Runtime injects an authoritative wall-clock time so models stop inventing dates."""

from __future__ import annotations

from agentengine.base.agent import AgentRun
from agentengine.base.context import AgentContext
from agentengine.memory.message import Message, Role
from agentengine.run_config import RunConfig
from agentengine.runtime.turn import (
    _ensure_runtime_time_context,
    _format_runtime_time_context,
)


def test_format_runtime_time_context_includes_date_and_guidance() -> None:
    text = _format_runtime_time_context()
    assert "Current date and time" in text
    assert "authoritative" in text
    assert "web-search" in text
    # YYYY-MM-DD appears at least once
    assert any(part.isdigit() and len(part) == 4 for part in text.replace("-", " ").split())


def test_ensure_runtime_time_context_appends_once_then_refreshes() -> None:
    context = AgentContext(request_id="r1", query="q")
    agent = AgentRun(config=RunConfig(name="t"), context=context)
    agent.memory.append(Message.system("base instructions"))

    _ensure_runtime_time_context(agent)
    first = [m for m in agent.memory.messages if m.metadata.get("runtime_context")]
    assert len(first) == 1
    assert first[0].role == Role.SYSTEM
    original = first[0].content

    # Mutate then refresh — still a single runtime_context message
    first[0].content = "stale"
    _ensure_runtime_time_context(agent)
    again = [m for m in agent.memory.messages if m.metadata.get("runtime_context")]
    assert len(again) == 1
    assert again[0].content != "stale"
    assert "Current date and time" in again[0].content
    # Content was rewritten from the stale placeholder
    assert again[0].content != original or "Current date and time" in again[0].content
