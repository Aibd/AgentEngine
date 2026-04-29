from dataclasses import dataclass
from typing import Any

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

        event = {
            "responseType": type_str,
            "response": data,
            "responseAll": data,
            "useTimes": 0,
            "reqId": self.request_id,
            "errorMsg": data if type_str == EventType.ERROR.value else None,
            "resultMap": data if is_dict and is_result_like else None,
            "conversation_id": self.conversation_id,
            "finished": bool(finished),
        }
        await self.event_stream.put(event)

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

    async def error(self, message: str) -> None:
        await self.send(EventType.ERROR, message, finished=True)

    async def done(self) -> None:
        await self.send(EventType.DONE, "done", finished=True)

    async def stream_chunk(self, content: str, finished: bool = False) -> None:
        await self.send(EventType.TEXT, content, finished=finished)
