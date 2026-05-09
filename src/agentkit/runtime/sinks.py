from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable
from typing import Protocol, cast

from agentkit.runtime.events import RuntimeEvent


EventCallback = Callable[[RuntimeEvent], Awaitable[None] | None]


class RuntimeEventSink(Protocol):
    """Consumer for semantic runtime events."""

    def consume(self, event: RuntimeEvent) -> Awaitable[None] | None:
        ...


SinkTarget = RuntimeEventSink | EventCallback


class RuntimeEventFanout:
    """Dispatch each RuntimeEvent to a list of sinks or callbacks."""

    def __init__(self, sinks: Iterable[SinkTarget] = ()) -> None:
        self._sinks = list(sinks)

    async def consume(self, event: RuntimeEvent) -> None:
        for sink in self._sinks:
            if hasattr(sink, "consume"):
                result = cast(RuntimeEventSink, sink).consume(event)
            else:
                result = sink(event)
            if inspect.isawaitable(result):
                await result
