from __future__ import annotations

from agentengine.runtime.events import RuntimeEvent, ToolCallCompleted
from agentengine.tools.base import Tool
from agentengine.tools.executor import ToolExecutor


class _LargeTool(Tool):
    name = "large"
    max_result_chars = 128

    async def run(self, **kwargs):
        return "x" * 100_000


class _HeadTailTool(Tool):
    name = "head_tail"
    max_result_chars = 20
    result_summary_strategy = "head_tail"

    async def run(self, **kwargs):
        return "a" * 50 + "z" * 50


async def test_tool_executor_truncates_oversized_result() -> None:
    events: list[RuntimeEvent] = []

    async def on_event(event: RuntimeEvent) -> None:
        events.append(event)

    result = await ToolExecutor(
        run_id="run_1",
        turn_id="turn_1",
        on_event=on_event,
    ).execute(_LargeTool(), {})

    assert result.ok is True
    assert result.raw == "x" * 100_000
    assert result.truncated is True
    assert len(result.content) < 100_000
    assert isinstance(events[-1], ToolCallCompleted)


async def test_tool_executor_head_tail_summary() -> None:
    result = await ToolExecutor(run_id="run_1", turn_id="turn_1").execute(
        _HeadTailTool(),
        {},
    )

    assert result.truncated is True
    assert result.content.startswith("a")
    assert result.content.endswith("z" * 10)
    assert "...[truncated]..." in result.content
