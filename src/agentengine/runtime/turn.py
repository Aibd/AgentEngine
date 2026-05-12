"""Single-loop turn execution.

`run_turn()` is the one and only think/act loop in the framework. It replaces
the former `ReActHandler` polymorphism: there is no handler dispatch, no
strategy class to register — orchestration calls this function directly.

All observable events flow through a single `emit(RuntimeEvent)` channel.
The SSE bridge (`Printer.from_runtime_event`) and any other sinks subscribe
to that channel - `run_turn()` itself never touches `context.printer`.

Mirrors the codex/claude-code-src pattern where the loop is a single function
parameterised by runtime data instead of a hierarchy of handler classes.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

from agentengine.base.agent import AgentRun
from agentengine.base.context import AgentContext
from agentengine.base.state import AgentState
from agentengine.errors import AgentCancelledError, ContextWindowExceededError
from agentengine.hooks import (
    AfterTurnPayload,
    HookAbortError,
    HookEvent,
    HookManager,
    PostToolUsePayload,
    PreToolUsePayload,
    UserPromptSubmitPayload,
)
from agentengine.llm.client import LLMResponse
from agentengine.memory.message import Message
from agentengine.runtime.cancellation import CancellationToken
from agentengine.runtime.compaction import Compactor, LLMSummaryCompactor
from agentengine.runtime.events import (
    ReasoningDelta,
    RuntimeEvent,
    TextDelta,
    ToolCallFailed,
    ToolCallStarted,
    TurnEnded,
    TurnStarted,
    UsageReport,
)
from agentengine.tools.executor import ToolExecutor
from agentengine.tools.policy import ExecPolicy
from agentengine.enterprise.approval import ApprovalDeniedError, ApprovalGate
from agentengine.enterprise.tenant import TenantContext
from agentengine.enterprise.quota import QuotaExceededError, QuotaStore
from agentengine.runtime.events import ApprovalRequired


DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
logger = logging.getLogger(__name__)

EmitFn = Callable[[RuntimeEvent], Awaitable[None] | None]


async def run_turn(
    agent: AgentRun,
    context: AgentContext,
    query: str,
    *,
    tool_timeout_seconds: float | None = DEFAULT_TOOL_TIMEOUT_SECONDS,
    emit: EmitFn | None = None,
) -> str:
    """Drive one agent run to completion.

    Owns lifecycle: setup → memory hydration → loop → state transition →
    teardown. Returns the final assistant content.

    `emit` receives every RuntimeEvent the loop produces. The default no-ops;
    `TurnRunner` supplies a real emitter that fans out to the SSE bridge and
    JSONL log.
    """
    if tool_timeout_seconds is not None and tool_timeout_seconds <= 0:
        raise ValueError("tool_timeout_seconds must be greater than 0")

    run_id = str(context.extras.get("run_id", context.request_id))
    turn_id = str(context.extras.get("turn_id", context.request_id))
    emit_event = _coerce_emit(emit)

    started_at = time.perf_counter()
    primary_error: BaseException | None = None

    try:
        await agent.setup()
        agent.state = AgentState.RUNNING

        logger.info("agent_run_start request_id=%s agent=%s", context.request_id, agent.name)

        if context.persistence and context.conversation_id:
            await agent.memory.load_from_db(context.persistence, context.conversation_id)

        result = await _loop(
            agent,
            context,
            query,
            tool_timeout_seconds=tool_timeout_seconds,
            emit=emit_event,
            run_id=run_id,
            turn_id=turn_id,
        )
        agent.state = AgentState.FINISHED
        logger.info(
            "agent_run_finish request_id=%s agent=%s steps=%d elapsed=%.3fs",
            context.request_id,
            agent.name,
            agent.current_step,
            time.perf_counter() - started_at,
        )
        return result
    except (asyncio.CancelledError, AgentCancelledError) as exc:
        primary_error = exc
        agent.state = AgentState.CANCELLED
        logger.warning(
            "agent_run_cancelled request_id=%s agent=%s steps=%d elapsed=%.3fs",
            context.request_id,
            agent.name,
            agent.current_step,
            time.perf_counter() - started_at,
        )
        raise
    except Exception as exc:
        primary_error = exc
        agent.state = AgentState.ERROR
        logger.exception(
            "agent_run_error request_id=%s agent=%s steps=%d elapsed=%.3fs",
            context.request_id,
            agent.name,
            agent.current_step,
            time.perf_counter() - started_at,
        )
        raise
    finally:
        if context.persistence and context.conversation_id:
            try:
                await agent.memory.save_to_db(context.persistence, context.conversation_id)
            except Exception:
                logger.exception(
                    "memory_save_error request_id=%s conversation_id=%s",
                    context.request_id,
                    context.conversation_id,
                )
        await _teardown(agent, context, primary_error)


def _coerce_emit(emit: EmitFn | None) -> Callable[[RuntimeEvent], Awaitable[None]]:
    async def _noop(event: RuntimeEvent) -> None:
        return None

    if emit is None:
        return _noop

    async def _wrapped(event: RuntimeEvent) -> None:
        result = emit(event)
        if inspect.isawaitable(result):
            await result

    return _wrapped


async def _teardown(
    agent: AgentRun,
    context: AgentContext,
    primary_error: BaseException | None,
) -> None:
    try:
        await agent.teardown()
    except Exception:
        logger.exception(
            "agent_teardown_error request_id=%s agent=%s",
            context.request_id,
            agent.name,
        )
        if primary_error is None:
            agent.state = AgentState.ERROR
            raise


async def _loop(
    agent: AgentRun,
    context: AgentContext,
    query: str,
    *,
    tool_timeout_seconds: float | None,
    emit: Callable[[RuntimeEvent], Awaitable[None]],
    run_id: str,
    turn_id: str,
) -> str:
    if context.llm is None:
        agent.memory.add_user_message(query)
        return ""

    hook_manager: HookManager | None = context.extras.get("hooks")
    cwd = str(context.extras.get("workspace_root") or "")
    session_id = (
        context.session_id
        or context.conversation_id
        or context.request_id
    )

    # UserPromptSubmit fires right before the user's query enters memory,
    # giving handlers a chance to vet/sanitize the prompt or abort the run
    # before any model invocation.
    if hook_manager is not None:
        await hook_manager.dispatch(
            HookEvent.USER_PROMPT_SUBMIT,
            UserPromptSubmitPayload(
                session_id=session_id,
                run_id=run_id,
                turn_id=turn_id,
                cwd=cwd,
                agent_name=agent.name,
                query=query,
            ),
        )

    agent.memory.add_user_message(query)

    final_answer = ""

    prompt_tokens_total = 0
    completion_tokens_total = 0
    loop_started_at = time.perf_counter()
    cancellation_token = _get_cancellation_token(context)

    while True:
        if cancellation_token is not None:
            cancellation_token.throw_if_cancelled()

        await _maybe_compact(agent, context)
        agent.current_step += 1
        turn_started_at = time.perf_counter()

        await emit(
            TurnStarted(run_id=run_id, turn_id=turn_id, turn=agent.current_step)
        )

        messages = agent.memory.snapshot()
        tools = (
            context.tool_collection.to_openai_tools()
            if context.tool_collection and len(context.tool_collection.tool_map) > 0
            else None
        )

        response = await _chat_streaming(
            context, messages, tools=tools, emit=emit, run_id=run_id, turn_id=turn_id
        )
        if cancellation_token is not None:
            cancellation_token.throw_if_cancelled()

        if response.usage:
            prompt_tokens_total += int(response.usage.get("prompt_tokens", 0) or 0)
            completion_tokens_total += int(response.usage.get("completion_tokens", 0) or 0)

        agent.memory.add_assistant_message(
            response.content or "",
            reasoning_content=response.reasoning_content or "",
            tool_calls=response.tool_calls or None,
        )

        if response.content:
            final_answer = response.content

        has_tool_calls = bool(response.tool_calls)
        turn_elapsed = time.perf_counter() - turn_started_at

        if has_tool_calls:
            if cancellation_token is not None:
                cancellation_token.throw_if_cancelled()
            await _execute_tool_calls(
                agent,
                context,
                response.tool_calls,
                tool_timeout_seconds=tool_timeout_seconds,
                emit=emit,
                run_id=run_id,
                turn_id=turn_id,
            )

        await emit(
            TurnEnded(
                run_id=run_id,
                turn_id=turn_id,
                turn=agent.current_step,
                has_tool_calls=has_tool_calls,
                elapsed_seconds=turn_elapsed,
            )
        )

        should_stop = await _dispatch_after_turn(
            hook_manager,
            session_id=session_id,
            run_id=run_id,
            turn_id=turn_id,
            cwd=cwd,
            agent=agent,
            has_tool_calls=has_tool_calls,
            final_answer=final_answer,
        )
        if should_stop and has_tool_calls:
            context.extras["terminal_reason"] = "hook_stopped"
            break

        if not has_tool_calls:
            break

    await emit(
        UsageReport(
            run_id=run_id,
            turn_id=turn_id,
            prompt_tokens=prompt_tokens_total,
            completion_tokens=completion_tokens_total,
            total_tokens=prompt_tokens_total + completion_tokens_total,
            total_seconds=time.perf_counter() - loop_started_at,
        )
    )
    quota_store = context.extras.get("_quota_store")
    quota_tenant_id = context.extras.get("_quota_tenant_id")
    if isinstance(quota_store, QuotaStore) and isinstance(quota_tenant_id, str):
        await quota_store.record_tokens(
            quota_tenant_id,
            prompt_tokens_total,
            completion_tokens_total,
        )

    return final_answer


def _get_cancellation_token(context: AgentContext) -> CancellationToken | None:
    token = context.extras.get("cancellation_token")
    return token if isinstance(token, CancellationToken) else None


async def _maybe_compact(agent: AgentRun, context: AgentContext) -> None:
    threshold = agent.config.auto_compact_tokens
    if threshold <= 0 or agent.memory.estimated_tokens() <= threshold:
        return

    compactor: Compactor | None = None
    configured = context.extras.get("compactor") or agent.config.compactor
    if configured is not None:
        compactor = cast(Compactor, configured)
    elif context.llm is not None:
        compactor = LLMSummaryCompactor(
            context.llm,
            keep_recent=agent.config.compaction_keep_recent,
        )

    if compactor is None:
        raise ContextWindowExceededError(
            "context window exceeded and no compactor is configured",
            details={"estimated_tokens": agent.memory.estimated_tokens(), "threshold": threshold},
        )

    try:
        compacted = await compactor.compact(agent.memory.snapshot())
    except ContextWindowExceededError:
        raise
    except Exception as exc:
        raise ContextWindowExceededError(
            f"auto-compaction failed: {exc}",
            details={
                "estimated_tokens": agent.memory.estimated_tokens(),
                "threshold": threshold,
                "cause_type": type(exc).__name__,
            },
        ) from exc

    agent.memory.replace(compacted)
    if agent.memory.estimated_tokens() > threshold:
        raise ContextWindowExceededError(
            "auto-compaction did not reduce history below the configured threshold",
            details={"estimated_tokens": agent.memory.estimated_tokens(), "threshold": threshold},
        )


async def _dispatch_after_turn(
    hook_manager: HookManager | None,
    *,
    session_id: str,
    run_id: str,
    turn_id: str,
    cwd: str,
    agent: AgentRun,
    has_tool_calls: bool,
    final_answer: str,
) -> bool:
    if hook_manager is None:
        return False
    results = await hook_manager.dispatch(
        HookEvent.AFTER_TURN,
        AfterTurnPayload(
            session_id=session_id,
            run_id=run_id,
            turn_id=turn_id,
            cwd=cwd,
            agent_name=agent.name,
            turn=agent.current_step,
            has_tool_calls=has_tool_calls,
            final_answer=final_answer,
        ),
    )
    return any(result.should_stop for result in results)


async def _chat_streaming(
    context: AgentContext,
    messages: list[Message],
    *,
    tools: list[dict[str, Any]] | None,
    emit: Callable[[RuntimeEvent], Awaitable[None]],
    run_id: str,
    turn_id: str,
) -> LLMResponse:
    if context.llm is None:
        return LLMResponse()

    chat_stream = getattr(context.llm, "chat_stream", None)
    if chat_stream is None:
        return await context.llm.chat(messages, tools=tools, stream=False)

    started_at = time.perf_counter()
    logger.debug(
        "llm_stream_start request_id=%s messages=%d tools=%d",
        context.request_id,
        len(messages),
        len(tools or []),
    )

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls_map: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    usage: dict[str, int] = {}
    raw_chunks: list[dict[str, Any]] = []

    try:
        async for chunk in chat_stream(messages, tools=tools):
            if chunk.content:
                content_parts.append(chunk.content)
                await emit(
                    TextDelta(run_id=run_id, turn_id=turn_id, content=chunk.content)
                )
            if chunk.reasoning_content:
                reasoning_parts.append(chunk.reasoning_content)
                await emit(
                    ReasoningDelta(
                        run_id=run_id, turn_id=turn_id, content=chunk.reasoning_content
                    )
                )
            if chunk.usage:
                usage = chunk.usage
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            if chunk.raw is not None:
                raw_chunks.append(chunk.raw)
                _accumulate_tool_calls(tool_calls_map, chunk.raw)
    except Exception:
        logger.exception(
            "llm_stream_error request_id=%s elapsed=%.3fs",
            context.request_id,
            time.perf_counter() - started_at,
        )
        raise

    logger.debug(
        "llm_stream_finish request_id=%s finish_reason=%s content_chars=%d tool_calls=%d elapsed=%.3fs",
        context.request_id,
        finish_reason or "stop",
        sum(len(part) for part in content_parts),
        len(tool_calls_map),
        time.perf_counter() - started_at,
    )

    return LLMResponse(
        content="".join(content_parts),
        reasoning_content="".join(reasoning_parts),
        tool_calls=list(tool_calls_map.values()) if tool_calls_map else [],
        finish_reason=finish_reason or "stop",
        usage=usage,
        raw={"chunks": raw_chunks} if raw_chunks else None,
    )


def _accumulate_tool_calls(
    tool_calls_map: dict[int, dict[str, Any]],
    raw: dict[str, Any],
) -> None:
    for choice in raw.get("choices", []) or []:
        delta = choice.get("delta", {}) or {}
        for tc in delta.get("tool_calls", []) or []:
            idx = tc.get("index", 0)
            slot = tool_calls_map.setdefault(
                idx,
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            if tc.get("id"):
                slot["id"] = tc["id"]
            func = tc.get("function", {}) or {}
            if func.get("name"):
                slot["function"]["name"] = func["name"]
            if func.get("arguments"):
                slot["function"]["arguments"] += func["arguments"]


async def _execute_tool_calls(
    agent: AgentRun,
    context: AgentContext,
    tool_calls: list[dict[str, Any]],
    *,
    tool_timeout_seconds: float | None,
    emit: Callable[[RuntimeEvent], Awaitable[None]],
    run_id: str,
    turn_id: str,
) -> None:
    executor = ToolExecutor(
        run_id=run_id,
        turn_id=turn_id,
        on_event=emit,
        timeout_seconds=tool_timeout_seconds,
        exec_policy=(
            context.extras.get("exec_policy")
            if isinstance(context.extras.get("exec_policy"), ExecPolicy)
            else None
        ),
    )
    hook_manager: HookManager | None = context.extras.get("hooks")
    cwd = str(context.extras.get("workspace_root") or "")
    session_id = (
        context.session_id
        or context.conversation_id
        or context.request_id
    )

    for tc in tool_calls:
        tool_name = tc.get("function", {}).get("name", "")
        args_raw = tc.get("function", {}).get("arguments", "")
        try:
            tool_args = (
                json.loads(args_raw)
                if isinstance(args_raw, str) and args_raw
                else (args_raw if isinstance(args_raw, dict) else {})
            )
        except json.JSONDecodeError:
            tool_args = {}

        tool = (
            context.tool_collection.get(tool_name)
            if context.tool_collection
            else None
        )

        # Check approval gate for destructive tools
        approval_gate: ApprovalGate | None = context.extras.get("approval_gate")
        if approval_gate is not None and tool is not None and tool.is_destructive:
            tenant_id = ""
            tenant = context.extras.get("tenant")
            if isinstance(tenant, TenantContext):
                tenant_id = tenant.tenant_id

            approval_id = f"apr_{tc.get('id', '') or 'unknown'}"
            await emit(
                ApprovalRequired(
                    run_id=run_id, turn_id=turn_id,
                    approval_id=approval_id, tool_name=tool_name,
                    arguments=tool_args, status="pending",
                )
            )

            try:
                await approval_gate.request_approval(
                    tool_name, tool_args,
                    run_id=run_id, tenant_id=tenant_id, approval_id=approval_id,
                )
            except ApprovalDeniedError as denied:
                await emit(
                    ApprovalRequired(
                        run_id=run_id, turn_id=turn_id,
                        approval_id=approval_id, tool_name=tool_name,
                        arguments=tool_args, status="denied",
                        reason=str(denied),
                    )
                )
                await emit(
                    ToolCallFailed(
                        run_id=run_id, turn_id=turn_id,
                        tool_call_id=tc.get("id", ""),
                        tool_name=tool_name,
                        error_type="ApprovalDenied",
                        error_message=str(denied),
                        elapsed_seconds=0.0,
                    )
                )
                agent.memory.add_tool_message(
                    f"Tool '{tool_name}' denied by approval gate: {denied}",
                    tool_call_id=tc.get("id", ""),
                )
                continue

        if tool is None:
            tool_call_id = tc.get("id", "") or ""
            rendered = f"Unknown tool: {tool_name}"
            logger.warning(
                "tool_call_missing request_id=%s tool=%s",
                context.request_id,
                tool_name,
            )
            await emit(
                ToolCallStarted(
                    run_id=run_id,
                    turn_id=turn_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=tool_args,
                )
            )
            await emit(
                ToolCallFailed(
                    run_id=run_id,
                    turn_id=turn_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    error_type="ToolNotFound",
                    error_message=rendered,
                    elapsed_seconds=0.0,
                )
            )
            agent.memory.add_tool_message(rendered, tool_call_id=tool_call_id)
            continue

        logger.info(
            "tool_call_start request_id=%s tool=%s arg_keys=%s",
            context.request_id,
            tool_name,
            sorted(tool_args.keys()),
        )
        quota_store = context.extras.get("_quota_store")
        quota_tenant_id = context.extras.get("_quota_tenant_id")
        if isinstance(quota_store, QuotaStore) and isinstance(quota_tenant_id, str):
            try:
                await quota_store.check_and_acquire_tool_call(quota_tenant_id)
            except QuotaExceededError as quota_error:
                tool_call_id = tc.get("id", "") or ""
                rendered = str(quota_error)
                await emit(
                    ToolCallStarted(
                        run_id=run_id,
                        turn_id=turn_id,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        arguments=tool_args,
                    )
                )
                await emit(
                    ToolCallFailed(
                        run_id=run_id,
                        turn_id=turn_id,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        error_type="QuotaExceededError",
                        error_message=rendered,
                        elapsed_seconds=0.0,
                    )
                )
                agent.memory.add_tool_message(rendered, tool_call_id=tool_call_id)
                continue

        # PreToolUse fires after the approval gate has accepted the call but
        # before the tool actually runs. Aborting here skips the tool and
        # threads the abort reason back as a ToolCallFailed event so the
        # model sees the rejection in memory.
        tool_call_id = tc.get("id", "") or ""
        if hook_manager is not None:
            try:
                await hook_manager.dispatch(
                    HookEvent.PRE_TOOL_USE,
                    PreToolUsePayload(
                        session_id=session_id,
                        run_id=run_id,
                        turn_id=turn_id,
                        cwd=cwd,
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        arguments=tool_args,
                    ),
                )
            except HookAbortError as abort:
                rendered = f"Tool '{tool_name}' blocked by hook: {abort.reason or 'no reason given'}"
                await emit(
                    ToolCallStarted(
                        run_id=run_id,
                        turn_id=turn_id,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        arguments=tool_args,
                    )
                )
                await emit(
                    ToolCallFailed(
                        run_id=run_id,
                        turn_id=turn_id,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        error_type="HookAbortError",
                        error_message=rendered,
                        elapsed_seconds=0.0,
                    )
                )
                agent.memory.add_tool_message(rendered, tool_call_id=tool_call_id)
                continue

        result = await executor.execute(
            tool,
            tool_args,
            tool_call_id=tc.get("id", "") or None,
        )
        if result.ok:
            logger.info(
                "tool_call_finish request_id=%s tool=%s elapsed=%.3fs truncated=%s",
                context.request_id,
                tool_name,
                result.elapsed_seconds,
                result.truncated,
            )
        else:
            logger.warning(
                "tool_call_failed request_id=%s tool=%s error=%s elapsed=%.3fs",
                context.request_id,
                tool_name,
                result.error,
                result.elapsed_seconds,
            )

        # PostToolUse fires regardless of outcome. Aborting here can't undo
        # the tool side effects, so abort errors are swallowed by the
        # dispatcher boundary in the same spirit as Stop.
        if hook_manager is not None:
            try:
                await hook_manager.dispatch(
                    HookEvent.POST_TOOL_USE,
                    PostToolUsePayload(
                        session_id=session_id,
                        run_id=run_id,
                        turn_id=turn_id,
                        cwd=cwd,
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        arguments=tool_args,
                        ok=result.ok,
                        result_summary=result.content,
                        error_type="" if result.ok else (result.error or "")[:64],
                        error_message="" if result.ok else (result.error or ""),
                        elapsed_seconds=result.elapsed_seconds,
                    ),
                )
            except HookAbortError:
                # Tool already executed; abort here just stops the chain.
                pass

        agent.memory.add_tool_message(result.content, tool_call_id=tc.get("id", ""))
