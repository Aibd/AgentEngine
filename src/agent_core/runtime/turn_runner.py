from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from agent_core.base.agent import AgentRun
from agent_core.base.context import AgentContext
from agent_core.errors import LLMContextWindowError, LLMError, ToolExecutionError, error_to_dict
from agent_core.observability.event_log import RunEventLog
from agent_core.observability.jsonl_sink import JsonlSink
from agent_core.runtime.events import (
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
)
from agent_core.runtime.run_state import RunState, TerminalReason
from agent_core.runtime.sinks import RuntimeEventFanout
from agent_core.runtime.turn import DEFAULT_TOOL_TIMEOUT_SECONDS, run_turn

EventCallback = Callable[[RuntimeEvent], Awaitable[None] | None]
TurnFn = Callable[[AgentRun, AgentContext, str], Awaitable[str]]


class TurnRunner:
    """Lifecycle manager for one agent turn.

    Wraps a turn function with run identifiers, state transitions, and the
    JSONL event log. Defaults to `run_turn()`; tests can substitute a custom
    callable to exercise lifecycle behaviour without driving an LLM.
    """

    def __init__(
        self,
        session_id: str = "",
        *,
        log_dir: str | Path | None = None,
        enable_event_log: bool = True,
    ) -> None:
        self.session_id = session_id
        self.log_dir = log_dir
        self.enable_event_log = enable_event_log

    async def run(
        self,
        *,
        agent: AgentRun,
        context: AgentContext,
        query: str,
        on_event: EventCallback | None = None,
        tool_timeout_seconds: float | None = DEFAULT_TOOL_TIMEOUT_SECONDS,
        turn_fn: TurnFn | None = None,
    ) -> str:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        session_id = self.session_id or context.session_id or context.conversation_id or context.request_id
        state = RunState(run_id=run_id, session_id=session_id, turn_id=turn_id)
        events: list[RuntimeEvent] = []
        event_log = (
            RunEventLog(run_id, base_dir=self.log_dir)
            if self.enable_event_log
            else None
        )
        fanout = RuntimeEventFanout(
            [
                *([JsonlSink(event_log)] if event_log is not None else []),
                *([on_event] if on_event is not None else []),
            ]
        )
        started_at = time.perf_counter()

        async def emit(event: RuntimeEvent) -> None:
            events.append(event)
            await fanout.consume(event)

        context.extras["run_id"] = run_id
        context.extras["turn_id"] = turn_id
        context.extras["runtime_events"] = events
        context.extras["run_state"] = state
        if event_log is not None:
            context.extras["run_event_log_path"] = str(event_log.path)

        state.mark_running()
        await emit(
            RunStarted(
                run_id=run_id,
                turn_id=turn_id,
                agent_name=agent.name,
                input_summary=query[:200],
            )
        )

        try:
            if turn_fn is None:
                result = await run_turn(
                    agent,
                    context,
                    query,
                    tool_timeout_seconds=tool_timeout_seconds,
                    emit=emit,
                )
            else:
                result = await turn_fn(agent, context, query)
        except asyncio.CancelledError:
            state.mark_cancelled()
            await emit(
                RunCancelled(
                    run_id=run_id,
                    turn_id=turn_id,
                    elapsed_seconds=time.perf_counter() - started_at,
                )
            )
            raise
        except Exception as exc:
            reason = self._classify(exc)
            state.mark_failed(reason)
            await emit(
                RunFailed(
                    run_id=run_id,
                    turn_id=turn_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    terminal_reason=reason.value,
                    elapsed_seconds=time.perf_counter() - started_at,
                    error_payload=error_to_dict(exc),
                )
            )
            raise

        state.mark_completed()
        await emit(
            RunCompleted(
                run_id=run_id,
                turn_id=turn_id,
                result_summary=result[:200],
                elapsed_seconds=time.perf_counter() - started_at,
            )
        )
        return result

    @staticmethod
    def _classify(error: BaseException) -> TerminalReason:
        if isinstance(error, LLMContextWindowError):
            return TerminalReason.CONTEXT_EXCEEDED
        if isinstance(error, ToolExecutionError):
            return TerminalReason.TOOL_FAILED
        if isinstance(error, LLMError):
            return TerminalReason.MODEL_FAILED
        return TerminalReason.RUNTIME_FAILED
