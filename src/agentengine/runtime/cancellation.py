from __future__ import annotations

import asyncio

from agentengine.errors import AgentCancelledError


class CancellationToken:
    """Cooperative cancellation signal shared by the engine and runtime loop."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.reason = "cancelled"

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str = "cancelled") -> None:
        self.reason = reason or "cancelled"
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()

    def throw_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise AgentCancelledError(self.reason)


__all__ = ["CancellationToken"]
