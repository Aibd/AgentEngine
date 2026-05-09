from __future__ import annotations

from dataclasses import dataclass

from agentkit.runtime.events import RuntimeEvent
from agentkit.stream.printer import Printer


@dataclass(slots=True)
class SseSink:
    """RuntimeEvent sink that renders SSE v2 frames."""

    printer: Printer | None

    async def consume(self, event: RuntimeEvent) -> None:
        if self.printer is None:
            return
        await self.printer.from_runtime_event(event)
