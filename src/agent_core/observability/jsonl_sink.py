from __future__ import annotations

from dataclasses import dataclass

from agent_core.observability.event_log import RunEventLog
from agent_core.runtime.events import RuntimeEvent


@dataclass(slots=True)
class JsonlSink:
    """Append RuntimeEvent records to the per-run JSONL log."""

    log: RunEventLog

    def consume(self, event: RuntimeEvent) -> None:
        self.log.append(event)
