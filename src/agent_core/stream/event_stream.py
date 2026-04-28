import asyncio
from collections.abc import AsyncIterator
from typing import Any


class EventStream:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._closed = False

    async def put(self, event: dict[str, Any]) -> None:
        if not self._closed:
            await self._queue.put(event)

    async def close(self) -> None:
        self._closed = True
        await self._queue.put(None)

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self._queue.get()
            if event is None:
                break
            yield event
