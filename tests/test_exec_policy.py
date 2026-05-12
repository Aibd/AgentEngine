from __future__ import annotations

from agentengine.runtime.events import ApprovalRequired, RuntimeEvent, ToolCallFailed
from agentengine.tools.base import Tool
from agentengine.tools.executor import ToolExecutor
from agentengine.tools.policy import ExecPolicy, ExecPolicyAction, ExecPolicyRule


class _ShellLikeTool(Tool):
    name = "bash"

    async def run(self, **kwargs):
        return "ran"


async def test_exec_policy_deny_blocks_tool_before_execution() -> None:
    events: list[RuntimeEvent] = []
    policy = ExecPolicy([
        ExecPolicyRule("rm ", ExecPolicyAction.DENY, "dangerous delete"),
    ])

    result = await ToolExecutor(
        run_id="run_1",
        turn_id="turn_1",
        on_event=events.append,
        exec_policy=policy,
    ).execute(_ShellLikeTool(), {"command": "rm -rf tmp"}, tool_call_id="tc_1")

    assert result.ok is False
    assert "denied by ExecPolicy" in result.content
    failed = next(event for event in events if isinstance(event, ToolCallFailed))
    assert failed.error_type == "ExecPolicyDenied"


async def test_exec_policy_ask_emits_approval_required() -> None:
    events: list[RuntimeEvent] = []
    policy = ExecPolicy([
        ExecPolicyRule("deploy", ExecPolicyAction.ASK, "production change"),
    ])

    result = await ToolExecutor(
        run_id="run_1",
        turn_id="turn_1",
        on_event=events.append,
        exec_policy=policy,
    ).execute(_ShellLikeTool(), {"command": "deploy prod"}, tool_call_id="tc_2")

    assert result.ok is False
    approval = next(event for event in events if isinstance(event, ApprovalRequired))
    assert approval.status == "pending"
    assert approval.reason == "production change"
