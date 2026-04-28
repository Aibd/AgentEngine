from dataclasses import dataclass
from typing import Any

from agent_core.stream.event_stream import EventStream


@dataclass(slots=True)
class Printer:
    request_id: str
    event_stream: EventStream
    conversation_id: str = ""

    async def send(self, event_type: str, data: Any, *, finished: bool = False) -> None:
        event = {
            "responseType": event_type,
            "response": data,
            "responseAll": data,
            "useTimes": 0,
            "reqId": self.request_id,
            "errorMsg": None,
            "resultMap": data if isinstance(data, dict) and event_type in {"result", "tool_result"} else None,
            "conversation_id": self.conversation_id,
            "finished": finished,
        }
        await self.event_stream.put(event)
