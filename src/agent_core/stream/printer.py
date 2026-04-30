from dataclasses import dataclass
from typing import Any

from agent_core.errors import AgentCoreError, error_to_dict
from agent_core.runtime.events import (
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
    TextDelta,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
)
from agent_core.stream.event_stream import EventStream
from agent_core.stream.events import EventType


_TERMINAL_TYPES = {EventType.ERROR, EventType.DONE, EventType.FINAL_RESULT}


@dataclass(slots=True)
class Printer:
    """Type-safe event emitter that pushes business-shaped events into an EventStream.

    The dict shape (responseType / response / responseAll / resultMap / useTimes /
    reqId / errorMsg / conversation_id / finished) matches the existing SSE
    contract so frontends do not need to change.
    """

    request_id: str
    event_stream: EventStream
    conversation_id: str = ""

    async def send(
        self,
        event_type: str | EventType,
        data: Any,
        *,
        finished: bool | None = None,
    ) -> None:
        type_str = event_type.value if isinstance(event_type, EventType) else str(event_type)
        if finished is None:
            try:
                finished = EventType(type_str) in _TERMINAL_TYPES
            except ValueError:
                finished = False

        is_dict = isinstance(data, dict)
        is_result_like = type_str in {EventType.RESULT.value, EventType.TOOL_RESULT.value, EventType.FINAL_RESULT.value}

        error_msg = _error_message(data) if type_str == EventType.ERROR.value else None

        event = {
            "responseType": type_str,
            "response": data,
            "responseAll": data,
            "useTimes": 0,
            "reqId": self.request_id,
            "errorMsg": error_msg,
            "resultMap": data if is_dict and is_result_like else None,
            "conversation_id": self.conversation_id,
            "finished": bool(finished),
        }
        await self.event_stream.put(event)

    async def from_runtime_event(self, event: RuntimeEvent) -> None:
        """Translate a runtime event into the existing SSE envelope."""
        if isinstance(event, RunStarted):
            await self.start(event.input_summary)
        elif isinstance(event, TextDelta):
            await self.text(event.content)
        elif isinstance(event, ToolCallStarted):
            await self.task(f"Tool started: {event.tool_name}")
        elif isinstance(event, ToolCallCompleted):
            await self.tool_result(event.tool_name, event.result_summary)
        elif isinstance(event, ToolCallFailed):
            await self.tool_result(
                event.tool_name,
                f"Tool error: {event.error_message}",
            )
        elif isinstance(event, RunCompleted):
            await self.result({"result": event.result_summary})
        elif isinstance(event, RunFailed):
            await self.error(
                {
                    "code": event.terminal_reason,
                    "message": event.error_message,
                    "category": "runtime",
                    "retryable": False,
                    "details": {"type": event.error_type},
                }
            )
        elif isinstance(event, RunCancelled):
            await self.error(
                {
                    "code": "cancelled",
                    "message": event.reason,
                    "category": "runtime",
                    "retryable": False,
                }
            )

    # -- Convenience shortcuts -----------------------------------------

    async def start(self, query: str) -> None:
        await self.send(EventType.START, query)

    async def text(self, content: str) -> None:
        await self.send(EventType.TEXT, content)

    async def task(self, description: str) -> None:
        await self.send(EventType.TASK, description)

    async def tool_thought(self, content: str) -> None:
        await self.send(EventType.TOOL_THOUGHT, {"tool_thought": content})

    async def tool_result(self, tool: str, result: Any) -> None:
        await self.send(EventType.TOOL_RESULT, {"tool": tool, "toolResult": result})

    async def result(self, data: Any) -> None:
        await self.send(EventType.RESULT, data, finished=True)

    async def error(self, error: str | BaseException | dict[str, Any]) -> None:
        data = error_to_dict(error) if isinstance(error, BaseException) else error
        await self.send(EventType.ERROR, data, finished=True)

    async def done(self) -> None:
        await self.send(EventType.DONE, "done", finished=True)

    async def stream_chunk(self, content: str, finished: bool = False) -> None:
        await self.send(EventType.TEXT, content, finished=finished)


def _error_message(data: Any) -> str:
    if isinstance(data, AgentCoreError):
        return data.message
    if isinstance(data, dict):
        message = data.get("message")
        return message if isinstance(message, str) else str(data)
    return str(data)
