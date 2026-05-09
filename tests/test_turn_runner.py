from __future__ import annotations

import asyncio

import pytest

from agentkit.base.agent import AgentRun
from agentkit.base.context import AgentContext
from agentkit.errors import LLMTimeoutError, ToolExecutionError
from agentkit.runtime.events import RunCancelled, RunCompleted, RunFailed, RunStarted, RuntimeEvent
from agentkit.runtime.run_state import RunStatus, TerminalReason
from agentkit.runtime.turn_runner import TurnRunner
from agentkit.spec import AgentSpec


async def _ok_turn(agent: AgentRun, context: AgentContext, query: str) -> str:
    return f"ok:{query}"


def _failing_turn(error: BaseException):
    async def _turn(agent: AgentRun, context: AgentContext, query: str) -> str:
        raise error

    return _turn


async def _run_with(turn_fn) -> tuple[list[RuntimeEvent], AgentContext, str | None]:
    context = AgentContext(request_id="req-1", query="hello")
    agent = AgentRun(spec=AgentSpec(name="turn_runner_test"), context=context)
    events: list[RuntimeEvent] = []

    async def on_event(event: RuntimeEvent) -> None:
        events.append(event)

    result = await TurnRunner("session-1").run(
        agent=agent,
        context=context,
        query="hello",
        on_event=on_event,
        turn_fn=turn_fn,
    )
    return events, context, result


async def test_turn_runner_happy_path_records_state_and_events() -> None:
    events, context, result = await _run_with(_ok_turn)

    assert result == "ok:hello"
    assert isinstance(events[0], RunStarted)
    assert isinstance(events[-1], RunCompleted)
    assert context.extras["run_id"].startswith("run_")
    assert context.extras["turn_id"].startswith("turn_")
    assert context.extras["runtime_events"] == events
    assert context.extras["run_state"].status == RunStatus.COMPLETED


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ToolExecutionError("tool broke"), TerminalReason.TOOL_FAILED),
        (LLMTimeoutError("model timed out"), TerminalReason.MODEL_FAILED),
        (RuntimeError("boom"), TerminalReason.RUNTIME_FAILED),
    ],
)
async def test_turn_runner_classifies_failures(
    error: BaseException,
    reason: TerminalReason,
) -> None:
    context = AgentContext(request_id="req-1", query="hello")
    agent = AgentRun(spec=AgentSpec(name="turn_runner_test"), context=context)
    events: list[RuntimeEvent] = []

    async def on_event(event: RuntimeEvent) -> None:
        events.append(event)

    with pytest.raises(type(error)):
        await TurnRunner("session-1").run(
            agent=agent,
            context=context,
            query="hello",
            on_event=on_event,
            turn_fn=_failing_turn(error),
        )

    assert isinstance(events[-1], RunFailed)
    assert events[-1].terminal_reason == reason.value
    assert context.extras["run_state"].status == RunStatus.FAILED
    assert context.extras["run_state"].terminal_reason == reason


async def test_turn_runner_records_cancelled() -> None:
    context = AgentContext(request_id="req-1", query="hello")
    agent = AgentRun(spec=AgentSpec(name="turn_runner_test"), context=context)
    events: list[RuntimeEvent] = []

    async def on_event(event: RuntimeEvent) -> None:
        events.append(event)

    with pytest.raises(asyncio.CancelledError):
        await TurnRunner("session-1").run(
            agent=agent,
            context=context,
            query="hello",
            on_event=on_event,
            turn_fn=_failing_turn(asyncio.CancelledError()),
        )

    assert isinstance(events[-1], RunCancelled)
    assert context.extras["run_state"].status == RunStatus.CANCELLED


async def test_turn_runner_generates_unique_ids() -> None:
    _, first_context, _ = await _run_with(_ok_turn)
    _, second_context, _ = await _run_with(_ok_turn)

    assert first_context.extras["run_id"] != second_context.extras["run_id"]
    assert first_context.extras["turn_id"] != second_context.extras["turn_id"]
