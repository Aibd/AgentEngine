from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from agentengine.base.agent import AgentRun
from agentengine.base.context import AgentContext
from agentengine.errors import (
    AgentCancelledError,
    ContextWindowExceededError,
    LLMError,
    ToolExecutionError,
    UsageLimitReachedError,
    error_to_dict,
)
from agentengine.enterprise.approval import ApprovalDeniedError
from agentengine.enterprise.middleware import MiddlewareChain
from agentengine.enterprise.quota import QuotaExceededError
from agentengine.hooks import (
    HookAbortError,
    HookEvent,
    HookManager,
    SessionStartPayload,
    StopPayload,
)
from agentengine.observability.event_log import RunEventLog
from agentengine.observability.jsonl_sink import JsonlSink
from agentengine.runtime.events import (
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
)
from agentengine.runtime.run_state import RunState, TerminalReason
from agentengine.runtime.sinks import RuntimeEventFanout
from agentengine.runtime.turn import DEFAULT_TOOL_TIMEOUT_SECONDS, run_turn
from agentengine.run_config import RunConfig

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
        middleware: MiddlewareChain | None = None,
        hook_manager: HookManager | None = None,
    ) -> None:
        self.session_id = session_id
        self.log_dir = log_dir
        self.enable_event_log = enable_event_log
        self.middleware = middleware
        self.hook_manager = hook_manager

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
        # Hook manager: runner explicit > context field > legacy extras >
        # nothing. We deliberately don't
        # fall back to the global default - that's opt-in via setup hook.
        hook_manager: HookManager | None = (
            self.hook_manager or context.hooks or context.extras.get("hooks")
        )
        if hook_manager is not None:
            context.hooks = hook_manager
            context.extras["hooks"] = hook_manager

        started_at = time.perf_counter()

        async def emit(event: RuntimeEvent) -> None:
            events.append(event)
            await fanout.consume(event)

        context.extras["run_id"] = run_id
        context.extras["turn_id"] = turn_id
        context.extras["runtime_events"] = events
        context.extras["run_state"] = state
        context.extras.pop("terminal_reason", None)
        # Expose the emit function so tools that need to surface side-channel
        # events (TodoWriteTool's TodosUpdated, AskUserQuestionTool's
        # UserQuestionAsked) can publish them without re-wrapping the fanout.
        context.extras["emit"] = emit
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

        cwd = context.workspace_root or str(context.extras.get("workspace_root") or "")
        # SessionStart fires once at the top of the run. Aborting here gives
        # users a clean way to gate runs (e.g. tenant quota check) before any
        # LLM cost is incurred.
        if hook_manager is not None:
            try:
                await hook_manager.dispatch(
                    HookEvent.SESSION_START,
                    SessionStartPayload(
                        session_id=session_id,
                        run_id=run_id,
                        turn_id=turn_id,
                        cwd=cwd,
                        agent_name=agent.name,
                        query_summary=query[:200],
                    ),
                )
            except HookAbortError as abort:
                state.mark_failed(TerminalReason.RUNTIME_FAILED)
                await emit(
                    RunFailed(
                        run_id=run_id,
                        turn_id=turn_id,
                        error_type="HookAbortError",
                        error_message=str(abort),
                        terminal_reason=TerminalReason.RUNTIME_FAILED.value,
                        elapsed_seconds=time.perf_counter() - started_at,
                    )
                )
                await self._emit_stop_hook(
                    hook_manager,
                    session_id=session_id,
                    run_id=run_id,
                    turn_id=turn_id,
                    cwd=cwd,
                    agent_name=agent.name,
                    status="failed",
                    elapsed=time.perf_counter() - started_at,
                )
                raise

        terminal_status = "completed"
        try:
            if self.middleware is not None:
                async def _inner(
                    config: RunConfig,
                    ctx: AgentContext,
                    q: str,
                ) -> str:
                    if turn_fn is None:
                        return await run_turn(
                            agent, ctx, q,
                            tool_timeout_seconds=tool_timeout_seconds,
                            emit=emit,
                        )
                    else:
                        return await turn_fn(agent, ctx, q)

                result = await self.middleware.run(
                    agent.config, context, query, inner=_inner,
                )
            elif turn_fn is None:
                result = await run_turn(
                    agent,
                    context,
                    query,
                    tool_timeout_seconds=tool_timeout_seconds,
                    emit=emit,
                )
            else:
                result = await turn_fn(agent, context, query)
        except (asyncio.CancelledError, AgentCancelledError):
            terminal_status = "cancelled"
            state.mark_cancelled()
            await emit(
                RunCancelled(
                    run_id=run_id,
                    turn_id=turn_id,
                    elapsed_seconds=time.perf_counter() - started_at,
                )
            )
            await self._emit_stop_hook(
                hook_manager,
                session_id=session_id,
                run_id=run_id,
                turn_id=turn_id,
                cwd=cwd,
                agent_name=agent.name,
                status=terminal_status,
                elapsed=time.perf_counter() - started_at,
            )
            raise
        except Exception as exc:
            terminal_status = "failed"
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
            await self._emit_stop_hook(
                hook_manager,
                session_id=session_id,
                run_id=run_id,
                turn_id=turn_id,
                cwd=cwd,
                agent_name=agent.name,
                status=terminal_status,
                elapsed=time.perf_counter() - started_at,
            )
            raise

        completion_reason = self._completion_reason(context)
        state.mark_completed(completion_reason)
        await emit(
            RunCompleted(
                run_id=run_id,
                turn_id=turn_id,
                result_summary=result[:200],
                terminal_reason=completion_reason.value,
                elapsed_seconds=time.perf_counter() - started_at,
            )
        )
        await self._emit_stop_hook(
            hook_manager,
            session_id=session_id,
            run_id=run_id,
            turn_id=turn_id,
            cwd=cwd,
            agent_name=agent.name,
            status=terminal_status,
            elapsed=time.perf_counter() - started_at,
        )
        return result

    @staticmethod
    async def _emit_stop_hook(
        manager: HookManager | None,
        *,
        session_id: str,
        run_id: str,
        turn_id: str,
        cwd: str,
        agent_name: str,
        status: str,
        elapsed: float,
    ) -> None:
        """Fire the Stop hook on every terminal path; never raises.

        Stop fires after the run is already concluding so abort has no
        meaningful effect — we still call dispatch (so handlers see the
        ``status``) but suppress :class:`HookAbortError` to avoid masking
        the original termination cause.
        """
        if manager is None:
            return
        payload = StopPayload(
            session_id=session_id,
            run_id=run_id,
            turn_id=turn_id,
            cwd=cwd,
            agent_name=agent_name,
            status=status,
            elapsed_seconds=elapsed,
        )
        try:
            await manager.dispatch(HookEvent.STOP, payload)
        except HookAbortError:
            # Stop handlers cannot abort an already-concluding run.
            pass

    @staticmethod
    def _classify(error: BaseException) -> TerminalReason:
        if isinstance(error, ContextWindowExceededError):
            return TerminalReason.CONTEXT_EXCEEDED
        if isinstance(error, UsageLimitReachedError):
            return TerminalReason.USAGE_LIMIT_REACHED
        if isinstance(error, ToolExecutionError):
            return TerminalReason.TOOL_FAILED
        if isinstance(error, LLMError):
            return TerminalReason.MODEL_FAILED
        if isinstance(error, ApprovalDeniedError):
            return TerminalReason.TOOL_FAILED
        if isinstance(error, QuotaExceededError):
            return TerminalReason.QUOTA_EXCEEDED
        return TerminalReason.RUNTIME_FAILED

    @staticmethod
    def _completion_reason(context: AgentContext) -> TerminalReason:
        raw = context.extras.get("terminal_reason")
        if isinstance(raw, TerminalReason):
            return raw
        if isinstance(raw, str):
            try:
                return TerminalReason(raw)
            except ValueError:
                return TerminalReason.NORMAL
        return TerminalReason.NORMAL
