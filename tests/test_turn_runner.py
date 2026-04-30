from __future__ import annotations

import asyncio

import pytest

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.errors import LLMTimeoutError, ToolExecutionError
from agent_core.handlers.base import AgentHandler
from agent_core.runtime.events import RunCancelled, RunCompleted, RunFailed, RunStarted, RuntimeEvent
from agent_core.runtime.run_state import RunStatus, TerminalReason
from agent_core.runtime.turn_runner import TurnRunner


class _OkHandler(AgentHandler):
    async def handle(self, agent: BaseAgent, context: AgentContext, query: str) -> str:
        return f"ok:{query}"


class _FailingHandler(AgentHandler):
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def handle(self, agent: BaseAgent, context: AgentContext, query: str) -> str:
        raise self.error


async def _run_with(handler: AgentHandler) -> tuple[list[RuntimeEvent], AgentContext, str | None]:
    context = AgentContext(request_id="req-1", query="hello")
    agent = BaseAgent(context)
    events: list[RuntimeEvent] = []
    result: str | None = None

    async def on_event(event: RuntimeEvent) -> None:
        events.append(event)

    result = await TurnRunner("session-1").run(
        agent=agent,
        handler=handler,
        context=context,
        query="hello",
        on_event=on_event,
    )
    return events, context, result


async def test_turn_runner_happy_path_records_state_and_events() -> None:
    events, context, result = await _run_with(_OkHandler())

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
    agent = BaseAgent(context)
    events: list[RuntimeEvent] = []

    async def on_event(event: RuntimeEvent) -> None:
        events.append(event)

    with pytest.raises(type(error)):
        await TurnRunner("session-1").run(
            agent=agent,
            handler=_FailingHandler(error),
            context=context,
            query="hello",
            on_event=on_event,
        )

    assert isinstance(events[-1], RunFailed)
    assert events[-1].terminal_reason == reason.value
    assert context.extras["run_state"].status == RunStatus.FAILED
    assert context.extras["run_state"].terminal_reason == reason


async def test_turn_runner_records_cancelled() -> None:
    context = AgentContext(request_id="req-1", query="hello")
    agent = BaseAgent(context)
    events: list[RuntimeEvent] = []

    async def on_event(event: RuntimeEvent) -> None:
        events.append(event)

    with pytest.raises(asyncio.CancelledError):
        await TurnRunner("session-1").run(
            agent=agent,
            handler=_FailingHandler(asyncio.CancelledError()),
            context=context,
            query="hello",
            on_event=on_event,
        )

    assert isinstance(events[-1], RunCancelled)
    assert context.extras["run_state"].status == RunStatus.CANCELLED


async def test_turn_runner_generates_unique_ids() -> None:
    _, first_context, _ = await _run_with(_OkHandler())
    _, second_context, _ = await _run_with(_OkHandler())

    assert first_context.extras["run_id"] != second_context.extras["run_id"]
    assert first_context.extras["turn_id"] != second_context.extras["turn_id"]
