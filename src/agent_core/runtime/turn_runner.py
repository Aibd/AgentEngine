from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from collections.abc import Awaitable, Callable

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.errors import LLMError, ToolExecutionError
from agent_core.handlers.base import AgentHandler
from agent_core.runtime.events import (
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
)
from agent_core.runtime.run_state import RunState, TerminalReason

EventCallback = Callable[[RuntimeEvent], Awaitable[None] | None]


class TurnRunner:
    """Lifecycle manager for one agent turn.

    Handlers still own the concrete ReAct/Pipeline/Legacy strategy. The runner
    wraps that call with run identifiers, state transitions, and runtime events.
    """

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id

    async def run(
        self,
        *,
        agent: BaseAgent,
        handler: AgentHandler,
        context: AgentContext,
        query: str,
        on_event: EventCallback | None = None,
    ) -> str:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        session_id = self.session_id or context.session_id or context.conversation_id or context.request_id
        state = RunState(run_id=run_id, session_id=session_id, turn_id=turn_id)
        events: list[RuntimeEvent] = []
        started_at = time.perf_counter()

        async def emit(event: RuntimeEvent) -> None:
            events.append(event)
            if on_event is None:
                return
            result = on_event(event)
            if inspect.isawaitable(result):
                await result

        context.extras["run_id"] = run_id
        context.extras["turn_id"] = turn_id
        context.extras["runtime_events"] = events
        context.extras["run_state"] = state

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
            result = await handler.handle(agent, context, query)
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
        if isinstance(error, ToolExecutionError):
            return TerminalReason.TOOL_FAILED
        if isinstance(error, LLMError):
            return TerminalReason.MODEL_FAILED
        return TerminalReason.RUNTIME_FAILED
