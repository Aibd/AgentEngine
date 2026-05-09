from __future__ import annotations

import logging

from agentengine.tools.base import Tool
from agentengine.tools.executor import ToolExecutor


class _DestructiveTool(Tool):
    name = "delete_everything"
    is_destructive = True

    async def run(self, **kwargs):
        return "not actually destructive"


async def test_tool_executor_logs_destructive_warning(caplog) -> None:
    caplog.set_level(logging.WARNING)

    result = await ToolExecutor(run_id="run_1", turn_id="turn_1").execute(
        _DestructiveTool(),
        {"path": "tmp"},
    )

    assert result.ok is True
    assert "destructive tool invoked" in caplog.text
