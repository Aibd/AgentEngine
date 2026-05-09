import asyncio
from collections.abc import AsyncIterator
from typing import Any, TypeAlias


SseFrame: TypeAlias = dict[str, Any]


class SseEventQueue:
    """Async queue of rendered SSE v2 frames."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[SseFrame | None] = asyncio.Queue()
        self._closed = False

    async def put(self, event: SseFrame) -> None:
        if not self._closed:
            await self._queue.put(event)

    async def put_event(self, event: str, data: Any) -> None:
        await self.put({"event": event, "data": data})

    async def put_comment(self, comment: str) -> None:
        await self.put({"comment": comment})

    async def close(self) -> None:
        self._closed = True
        await self._queue.put(None)

    async def __aiter__(self) -> AsyncIterator[SseFrame]:
        while True:
            event = await self._queue.get()
            if event is None:
                break
            yield event
