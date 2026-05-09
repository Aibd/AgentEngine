from dataclasses import dataclass
from typing import Any

from agentengine.errors import error_to_dict
from agentengine.runtime.events import (
    ReasoningDelta,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
    TextDelta,
    TodosUpdated,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
    ToolStreamEventEmitted,
    TurnEnded,
    TurnStarted,
    UsageReport,
    UserQuestionAsked,
)
from agentengine.stream.events import EventType
from agentengine.stream.sse_queue import SseEventQueue


@dataclass(slots=True)
class Printer:
    """Render runtime events into SSE v2 frames.

    Queue entries use this shape:
    `{"event": "<name>", "data": {...}}`.
    The HTTP layer turns them into `event:` / `data:` lines.
    """

    request_id: str
    event_stream: SseEventQueue
    conversation_id: str = ""

    async def send(self, event_type: str | EventType, data: Any) -> None:
        type_str = event_type.value if isinstance(event_type, EventType) else str(event_type)
        await self.event_stream.put_event(type_str, self._with_base_meta(data))

    async def from_runtime_event(self, event: RuntimeEvent) -> None:
        """Translate a RuntimeEvent into a v2 SSE frame."""
        if isinstance(event, RunStarted):
            await self.event_stream.put_comment(
                " ".join(
                    [
                        f"run_id={event.run_id}",
                        f"turn_id={event.turn_id}",
                        f"request_id={self.request_id}",
                        f"conversation_id={self.conversation_id}",
                    ]
                )
            )
            await self._runtime_frame(
                EventType.START,
                event,
                {
                    "query": event.input_summary,
                    "agent": event.agent_name,
                },
            )
        elif isinstance(event, TurnStarted):
            await self._runtime_frame(EventType.STEP, event, {"turn": event.turn})
        elif isinstance(event, TurnEnded):
            await self._runtime_frame(
                EventType.STEP_END,
                event,
                {
                    "turn": event.turn,
                    "has_tool_calls": event.has_tool_calls,
                    "elapsed_ms": _seconds_to_ms(event.elapsed_seconds),
                },
            )
        elif isinstance(event, ReasoningDelta):
            await self._runtime_frame(EventType.THINKING, event, {"delta": event.content})
        elif isinstance(event, TextDelta):
            await self._runtime_frame(EventType.TEXT, event, {"delta": event.content})
        elif isinstance(event, ToolCallStarted):
            await self._runtime_frame(
                EventType.TOOL_CALL_START,
                event,
                {
                    "tool": event.tool_name,
                    "arguments": event.arguments,
                    "tool_call_id": event.tool_call_id,
                },
            )
        elif isinstance(event, ToolStreamEventEmitted):
            await self._runtime_frame(
                event.stream_event_type,
                event,
                event.data,
            )
        elif isinstance(event, ToolCallCompleted):
            await self._runtime_frame(
                EventType.TOOL_RESULT,
                event,
                {
                    "tool": event.tool_name,
                    "ok": True,
                    "result": event.result_summary,
                    "elapsed_ms": _seconds_to_ms(event.elapsed_seconds),
                    "tool_call_id": event.tool_call_id,
                },
            )
        elif isinstance(event, ToolCallFailed):
            await self._runtime_frame(
                EventType.TOOL_RESULT,
                event,
                {
                    "tool": event.tool_name,
                    "ok": False,
                    "result": f"Tool error: {event.error_message}",
                    "elapsed_ms": _seconds_to_ms(event.elapsed_seconds),
                    "tool_call_id": event.tool_call_id,
                    "error_type": event.error_type,
                    "error_message": event.error_message,
                },
            )
        elif isinstance(event, UsageReport):
            await self._runtime_frame(
                EventType.USAGE,
                event,
                {
                    "prompt_tokens": event.prompt_tokens,
                    "completion_tokens": event.completion_tokens,
                    "total_tokens": event.total_tokens,
                    "total_seconds": event.total_seconds,
                    "elapsed_ms": _seconds_to_ms(event.total_seconds),
                },
            )
        elif isinstance(event, RunCompleted):
            await self._runtime_frame(
                EventType.DONE,
                event,
                {
                    "reason": "completed",
                    "result": event.result_summary,
                    "elapsed_ms": _seconds_to_ms(event.elapsed_seconds),
                },
            )
        elif isinstance(event, RunFailed):
            await self._runtime_frame(
                EventType.ERROR,
                event,
                event.error_payload
                if event.error_payload is not None
                else {
                    "code": event.terminal_reason,
                    "message": event.error_message,
                    "category": "runtime",
                    "retryable": False,
                    "details": {"type": event.error_type},
                },
            )
        elif isinstance(event, TodosUpdated):
            await self._runtime_frame(
                EventType.TODOS_UPDATED,
                event,
                {"todos": event.todos},
            )
        elif isinstance(event, UserQuestionAsked):
            await self._runtime_frame(
                EventType.USER_QUESTION_ASKED,
                event,
                {
                    "question_id": event.question_id,
                    "question": event.question,
                    "options": event.options,
                    "multiple": event.multiple,
                },
            )
        elif isinstance(event, RunCancelled):
            await self._runtime_frame(
                EventType.ERROR,
                event,
                {
                    "code": "cancelled",
                    "message": event.reason,
                    "category": "runtime",
                    "retryable": False,
                    "elapsed_ms": _seconds_to_ms(event.elapsed_seconds),
                },
            )

    async def start(self, query: str) -> None:
        await self.send(EventType.START, {"query": query})

    async def text(self, content: str) -> None:
        await self.send(EventType.TEXT, {"delta": content})

    async def task(self, description: str) -> None:
        await self.send(EventType.TASK, {"description": description})

    async def tool_thought(self, content: str) -> None:
        await self.send(EventType.TOOL_THOUGHT, {"content": content})

    async def tool_result(
        self,
        tool: str,
        result: Any,
        *,
        ok: bool = True,
        elapsed_seconds: float = 0.0,
        tool_call_id: str = "",
        error_type: str = "",
    ) -> None:
        payload: dict[str, Any] = {
            "tool": tool,
            "ok": ok,
            "result": result,
            "elapsed_ms": _seconds_to_ms(elapsed_seconds),
            "tool_call_id": tool_call_id,
        }
        if error_type:
            payload["error_type"] = error_type
        await self.send(EventType.TOOL_RESULT, payload)

    async def thinking(self, content: str) -> None:
        await self.send(EventType.THINKING, {"delta": content})

    async def tool_call_start(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        tool_call_id: str = "",
    ) -> None:
        await self.send(
            EventType.TOOL_CALL_START,
            {"tool": tool, "arguments": arguments or {}, "tool_call_id": tool_call_id},
        )

    async def step(self, turn: int) -> None:
        await self.send(EventType.STEP, {"turn": turn})

    async def step_end(
        self,
        turn: int,
        has_tool_calls: bool,
        elapsed_seconds: float = 0.0,
    ) -> None:
        await self.send(
            EventType.STEP_END,
            {
                "turn": turn,
                "has_tool_calls": bool(has_tool_calls),
                "elapsed_ms": _seconds_to_ms(elapsed_seconds),
            },
        )

    async def usage(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        total_seconds: float = 0.0,
    ) -> None:
        await self.send(
            EventType.USAGE,
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "total_seconds": total_seconds,
                "elapsed_ms": _seconds_to_ms(total_seconds),
            },
        )

    async def result(self, data: Any) -> None:
        result = data.get("result", str(data)) if isinstance(data, dict) else str(data)
        await self.send(EventType.DONE, {"reason": "completed", "result": result})

    async def error(self, error: str | BaseException | dict[str, Any]) -> None:
        data = error_to_dict(error) if isinstance(error, BaseException) else error
        if isinstance(data, str):
            data = {"code": "runtime_execution_error", "message": data}
        await self.send(EventType.ERROR, data)

    async def done(self) -> None:
        await self.send(EventType.DONE, {"reason": "completed"})

    async def stream_chunk(self, content: str, finished: bool = False) -> None:
        await self.text(content)
        if finished:
            await self.done()

    async def _runtime_frame(
        self,
        event_type: str | EventType,
        event: RuntimeEvent,
        data: Any,
    ) -> None:
        type_str = event_type.value if isinstance(event_type, EventType) else str(event_type)
        await self.event_stream.put_event(
            type_str,
            self._with_base_meta(
                _ensure_mapping(data),
                run_id=event.run_id,
                turn_id=event.turn_id,
            ),
        )

    def _with_base_meta(
        self,
        data: Any,
        *,
        run_id: str = "",
        turn_id: str = "",
    ) -> Any:
        if isinstance(data, dict):
            payload = dict(data)
        else:
            payload = {"value": data}
        payload.setdefault("request_id", self.request_id)
        payload.setdefault("conversation_id", self.conversation_id)
        if run_id:
            payload.setdefault("run_id", run_id)
        if turn_id:
            payload.setdefault("turn_id", turn_id)
        return payload


def _ensure_mapping(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    return {"value": data}


def _seconds_to_ms(value: float) -> int:
    return max(0, int(round(value * 1000)))
