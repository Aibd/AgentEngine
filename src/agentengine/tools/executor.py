from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agentengine.runtime.events import (
    RuntimeEvent,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
    ToolStreamEventEmitted,
)
from agentengine.tools.base import StreamingTool, Tool, ToolStreamEvent

logger = logging.getLogger(__name__)

EventCallback = Callable[[RuntimeEvent], Awaitable[None] | None]


@dataclass(slots=True)
class ToolExecutionResult:
    tool_name: str
    ok: bool
    content: str
    raw: Any | None = None
    error: str | None = None
    truncated: bool = False
    elapsed_seconds: float = 0.0
    tool_call_id: str = ""


class ToolExecutor:
    """Uniform entry point for tool execution concerns."""

    def __init__(
        self,
        *,
        run_id: str,
        turn_id: str,
        on_event: EventCallback | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.run_id = run_id
        self.turn_id = turn_id
        self.on_event = on_event
        self.timeout_seconds = timeout_seconds

    async def execute(
        self,
        tool: Tool,
        arguments: dict[str, Any],
        *,
        tool_call_id: str | None = None,
    ) -> ToolExecutionResult:
        if isinstance(tool, StreamingTool):
            return await self._execute_streaming(tool, arguments, tool_call_id=tool_call_id)
        return await self._execute_plain(tool, arguments, tool_call_id=tool_call_id)

    # -- Plain (non-streaming) execution ----------------------------------

    async def _execute_plain(
        self,
        tool: Tool,
        arguments: dict[str, Any],
        *,
        tool_call_id: str | None = None,
    ) -> ToolExecutionResult:
        call_id = tool_call_id or f"tc_{uuid.uuid4().hex[:10]}"
        await self._emit_started(call_id, tool.name, arguments)
        if tool.is_destructive:
            logger.warning(
                "destructive tool invoked: name=%s arg_keys=%s",
                tool.name,
                sorted(arguments.keys()),
            )

        timeout = self.timeout_seconds if self.timeout_seconds is not None else tool.timeout_seconds
        started_at = time.perf_counter()
        try:
            raw = await asyncio.wait_for(tool.run(**arguments), timeout=timeout)
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - started_at
            message = f"Tool timeout after {timeout}s: {tool.name}"
            await self._emit_failed(call_id, tool.name, "TimeoutError", message, elapsed)
            return ToolExecutionResult(
                tool_name=tool.name, ok=False, content=message, error=message,
                elapsed_seconds=elapsed, tool_call_id=call_id,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - started_at
            message = f"Tool error: {exc}"
            await self._emit_failed(call_id, tool.name, type(exc).__name__, str(exc), elapsed)
            return ToolExecutionResult(
                tool_name=tool.name, ok=False, content=message, error=message,
                elapsed_seconds=elapsed, tool_call_id=call_id,
            )

        elapsed = time.perf_counter() - started_at
        content, truncated = self._summarize(raw, tool)
        await self._emit_completed(call_id, tool.name, content, elapsed)
        return ToolExecutionResult(
            tool_name=tool.name, ok=True, content=content, raw=raw,
            truncated=truncated, elapsed_seconds=elapsed, tool_call_id=call_id,
        )

    # -- Streaming execution ----------------------------------------------

    async def _execute_streaming(
        self,
        tool: StreamingTool,
        arguments: dict[str, Any],
        *,
        tool_call_id: str | None = None,
    ) -> ToolExecutionResult:
        call_id = tool_call_id or f"tc_{uuid.uuid4().hex[:10]}"
        await self._emit_started(call_id, tool.name, arguments)
        if tool.is_destructive:
            logger.warning(
                "destructive tool invoked: name=%s arg_keys=%s",
                tool.name,
                sorted(arguments.keys()),
            )

        timeout = self.timeout_seconds if self.timeout_seconds is not None else tool.timeout_seconds
        started_at = time.perf_counter()
        accumulated_parts: list[str] = []
        final_data: Any = None
        error_holder: list[BaseException] = []

        async def _consume() -> None:
            nonlocal final_data
            async for event in tool.run_stream(**arguments):
                if not event.is_final:
                    await self._emit_stream_event(call_id, tool.name, event)

                if event.is_final:
                    final_data = event.data
                else:
                    accumulated_parts.append(str(event.data))

        try:
            await asyncio.wait_for(_consume(), timeout=timeout)
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - started_at
            message = f"Tool timeout after {timeout}s: {tool.name}"
            await self._emit_failed(call_id, tool.name, "TimeoutError", message, elapsed)
            return ToolExecutionResult(
                tool_name=tool.name, ok=False, content=message, error=message,
                elapsed_seconds=elapsed, tool_call_id=call_id,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - started_at
            message = f"Tool error: {exc}"
            await self._emit_failed(call_id, tool.name, type(exc).__name__, str(exc), elapsed)
            return ToolExecutionResult(
                tool_name=tool.name, ok=False, content=message, error=message,
                elapsed_seconds=elapsed, tool_call_id=call_id,
            )

        elapsed = time.perf_counter() - started_at

        # Build the final content: prefer final_data, fallback to accumulated.
        if final_data is not None:
            content, truncated = self._summarize(final_data, tool)
        else:
            raw_text = "\n".join(accumulated_parts)
            content, truncated = self._summarize(raw_text, tool)

        await self._emit_completed(call_id, tool.name, content, elapsed)
        return ToolExecutionResult(
            tool_name=tool.name, ok=True, content=content,
            raw=final_data if final_data is not None else accumulated_parts,
            truncated=truncated, elapsed_seconds=elapsed, tool_call_id=call_id,
        )

    # -- Event emission helpers -------------------------------------------

    async def _emit_started(
        self,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        await self._emit(
            ToolCallStarted(
                run_id=self.run_id, turn_id=self.turn_id,
                tool_call_id=call_id, tool_name=tool_name, arguments=arguments,
            )
        )

    async def _emit_stream_event(
        self,
        call_id: str,
        tool_name: str,
        event: ToolStreamEvent,
    ) -> None:
        await self._emit(
            ToolStreamEventEmitted(
                run_id=self.run_id,
                turn_id=self.turn_id,
                tool_call_id=call_id,
                tool_name=tool_name,
                stream_event_type=event.event_type,
                data=event.data,
                is_final=event.is_final,
            )
        )

    async def _emit_completed(self, call_id: str, tool_name: str, content: str, elapsed: float) -> None:
        await self._emit(
            ToolCallCompleted(
                run_id=self.run_id, turn_id=self.turn_id,
                tool_call_id=call_id, tool_name=tool_name,
                result_summary=content, elapsed_seconds=elapsed,
            )
        )

    async def _emit_failed(self, call_id: str, tool_name: str, error_type: str, message: str, elapsed: float) -> None:
        await self._emit(
            ToolCallFailed(
                run_id=self.run_id, turn_id=self.turn_id,
                tool_call_id=call_id, tool_name=tool_name,
                error_type=error_type, error_message=message, elapsed_seconds=elapsed,
            )
        )

    async def _emit(self, event: RuntimeEvent) -> None:
        if self.on_event is None:
            return
        result = self.on_event(event)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _render(raw: Any) -> str:
        if isinstance(raw, str):
            return raw
        return json.dumps(raw, ensure_ascii=False)

    @classmethod
    def _summarize(cls, raw: Any, tool: Tool) -> tuple[str, bool]:
        rendered = cls._render(raw)
        if tool.result_summary_strategy == "none" or len(rendered) <= tool.max_result_chars:
            return rendered, False
        if tool.result_summary_strategy == "head_tail":
            head_chars = max(1, tool.max_result_chars // 2)
            tail_chars = max(1, tool.max_result_chars - head_chars)
            return (
                f"{rendered[:head_chars]}\n...[truncated]...\n{rendered[-tail_chars:]}",
                True,
            )
        return f"{rendered[:tool.max_result_chars]}\n...[truncated]", True
