from __future__ import annotations

from dataclasses import dataclass

from agent_core.runtime.events import RuntimeEvent
from agent_core.stream.printer import Printer


@dataclass(slots=True)
class SseSink:
    """RuntimeEvent sink that renders SSE v2 frames."""

    printer: Printer | None

    async def consume(self, event: RuntimeEvent) -> None:
        if self.printer is None:
            return
        await self.printer.from_runtime_event(event)
