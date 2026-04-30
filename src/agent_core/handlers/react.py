from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.base.state import AgentState
from agent_core.errors import error_to_dict
from agent_core.handlers.base import AgentHandler
from agent_core.llm.client import LLMResponse
from agent_core.memory.message import Message
from agent_core.registry.handler_registry import register_handler
from agent_core.runtime.events import RuntimeEvent
from agent_core.stream.events import EventType
from agent_core.tools.executor import ToolExecutor


DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
logger = logging.getLogger(__name__)


@register_handler("react")
class ReActHandler(AgentHandler):
    """Standard ReAct loop: LLM proposes tool calls, handler executes them,
    results feed back into memory until the LLM emits a content-only reply.

    Drives BaseAgent purely through its memory and hooks; the agent does not
    implement think/act itself.
    """

    name = "react"

    def __init__(
        self,
        *,
        tool_timeout_seconds: float | None = DEFAULT_TOOL_TIMEOUT_SECONDS,
    ) -> None:
        if tool_timeout_seconds is not None and tool_timeout_seconds <= 0:
            raise ValueError("tool_timeout_seconds must be greater than 0")
        self.tool_timeout_seconds = tool_timeout_seconds

    async def handle(self, agent: BaseAgent, context: AgentContext, query: str) -> str:
        started_at = time.perf_counter()
        primary_error: BaseException | None = None

        try:
            agent.setup()
            agent.state = AgentState.RUNNING

            logger.info(
                "react_run_start request_id=%s agent=%s max_steps=%d",
                context.request_id,
                agent.name,
                agent.max_steps,
            )

            if context.printer:
                await context.printer.send(EventType.START, query)

            result = await self._loop(agent, context, query)
            agent.state = AgentState.FINISHED
            logger.info(
                "react_run_finish request_id=%s agent=%s steps=%d elapsed=%.3fs",
                context.request_id,
                agent.name,
                agent.current_step,
                time.perf_counter() - started_at,
            )
            if context.printer:
                await context.printer.send(EventType.RESULT, {"result": result}, finished=True)
            return result
        except asyncio.CancelledError as exc:
            primary_error = exc
            agent.state = AgentState.CANCELLED
            logger.warning(
                "react_run_cancelled request_id=%s agent=%s steps=%d elapsed=%.3fs",
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
                "react_run_error request_id=%s agent=%s steps=%d elapsed=%.3fs",
                context.request_id,
                agent.name,
                agent.current_step,
                time.perf_counter() - started_at,
            )
            if context.printer:
                await context.printer.send(EventType.ERROR, error_to_dict(exc), finished=True)
            raise
        finally:
            await self._teardown(agent, context, primary_error)

    async def _teardown(
        self,
        agent: BaseAgent,
        context: AgentContext,
        primary_error: BaseException | None,
    ) -> None:
        try:
            await agent.teardown()
        except Exception as exc:
            logger.exception(
                "agent_teardown_error request_id=%s agent=%s",
                context.request_id,
                agent.name,
            )
            if primary_error is None:
                agent.state = AgentState.ERROR
                if context.printer:
                    await context.printer.send(
                        EventType.ERROR,
                        error_to_dict(exc),
                        finished=True,
                    )
                raise

    async def _loop(self, agent: BaseAgent, context: AgentContext, query: str) -> str:
        if context.llm is None:
            agent.memory.add_user_message(query)
            return ""

        sys_prompt = agent.system_prompt()
        if sys_prompt:
            agent.memory.add_system_message(sys_prompt)
        agent.memory.add_user_message(query)

        next_step = agent.next_step_prompt()
        final_answer = ""

        for _ in range(agent.max_steps):
            agent.current_step += 1

            messages = self._build_messages(agent, next_step)
            tools = (
                context.tool_collection.to_openai_tools()
                if context.tool_collection and len(context.tool_collection.tool_map) > 0
                else None
            )

            response = await self._chat_streaming(context, messages, tools=tools)

            agent.memory.add_assistant_message(
                response.content or "",
                reasoning_content=response.reasoning_content or "",
                tool_calls=response.tool_calls or None,
            )

            if response.content:
                final_answer = response.content

            if not response.tool_calls:
                break

            await self._execute_tool_calls(agent, context, response.tool_calls)

        return final_answer

    async def _chat_streaming(
        self,
        context: AgentContext,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None,
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
                    if context.printer:
                        await context.printer.send(EventType.TEXT, chunk.content)
                if chunk.reasoning_content:
                    reasoning_parts.append(chunk.reasoning_content)
                if chunk.usage:
                    usage = chunk.usage
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                if chunk.raw is not None:
                    raw_chunks.append(chunk.raw)
                    self._accumulate_tool_calls(tool_calls_map, chunk.raw)
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
        self,
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

    def _build_messages(self, agent: BaseAgent, next_step: str) -> list[Message]:
        if not next_step:
            return agent.memory.snapshot()

        # Inject next_step guidance before the last user message
        msgs = agent.memory.snapshot()
        insert_at = len(msgs)
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].role.value == "user":
                insert_at = i
                break
        msgs.insert(insert_at, Message.system(next_step))
        return msgs

    async def _execute_tool_calls(
        self,
        agent: BaseAgent,
        context: AgentContext,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        runtime_events = context.extras.setdefault("runtime_events", [])

        async def on_tool_event(event: RuntimeEvent) -> None:
            runtime_events.append(event)

        executor = ToolExecutor(
            run_id=str(context.extras.get("run_id", context.request_id)),
            turn_id=str(context.extras.get("turn_id", context.request_id)),
            on_event=on_tool_event,
            timeout_seconds=self.tool_timeout_seconds,
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
            if tool is None:
                rendered = f"Unknown tool: {tool_name}"
                logger.warning(
                    "tool_call_missing request_id=%s tool=%s",
                    context.request_id,
                    tool_name,
                )
            else:
                started_at = time.perf_counter()
                logger.info(
                    "tool_call_start request_id=%s tool=%s arg_keys=%s",
                    context.request_id,
                    tool_name,
                    sorted(tool_args.keys()),
                )
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
                        time.perf_counter() - started_at,
                        result.truncated,
                    )
                else:
                    logger.warning(
                        "tool_call_failed request_id=%s tool=%s error=%s elapsed=%.3fs",
                        context.request_id,
                        tool_name,
                        result.error,
                        time.perf_counter() - started_at,
                    )
                rendered = result.content

            agent.memory.add_tool_message(rendered, tool_call_id=tc.get("id", ""))

            if context.printer:
                await context.printer.send(
                    EventType.TOOL_RESULT,
                    {"tool": tool_name, "toolResult": rendered},
                )
