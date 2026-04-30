from __future__ import annotations

import asyncio

from agent_core.runtime.events import ToolCallFailed, ToolCallStarted, RuntimeEvent
from agent_core.tools.base import Tool
from agent_core.tools.executor import ToolExecutor


class _SlowTool(Tool):
    name = "slow"
    timeout_seconds = 0.01

    async def run(self, **kwargs):
        await asyncio.sleep(1)
        return "late"


async def test_tool_executor_timeout_returns_failure_result() -> None:
    events: list[RuntimeEvent] = []

    async def on_event(event: RuntimeEvent) -> None:
        events.append(event)

    result = await ToolExecutor(
        run_id="run_1",
        turn_id="turn_1",
        on_event=on_event,
    ).execute(_SlowTool(), {})

    assert result.ok is False
    assert result.error is not None
    assert "timeout" in result.error
    assert isinstance(events[0], ToolCallStarted)
    assert isinstance(events[-1], ToolCallFailed)
