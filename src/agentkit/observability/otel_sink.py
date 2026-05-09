from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from agentkit.runtime.events import RuntimeEvent

logger = logging.getLogger(__name__)
OtelAttribute = str | bool | int | float


@dataclass(slots=True)
class OTelSink:
    """Best-effort OpenTelemetry sink for RuntimeEvent breadcrumbs.

    OpenTelemetry is optional for this package. When the dependency is not
    installed, this sink quietly becomes a no-op.
    """

    enabled: bool = True

    def consume(self, event: RuntimeEvent) -> None:
        if not self.enabled:
            return
        try:
            trace = importlib.import_module("opentelemetry.trace")
            span = trace.get_current_span()
            is_recording = getattr(span, "is_recording", lambda: False)
            if not is_recording():
                return
            span.add_event(
                f"agentkit.{event.event_type}",
                attributes=_otel_attributes(event.to_dict()),
            )
        except ModuleNotFoundError:
            return
        except Exception:
            logger.debug("otel sink failed for event=%s", event.event_type, exc_info=True)


def _otel_attributes(data: dict[str, Any]) -> dict[str, OtelAttribute]:
    attrs: dict[str, OtelAttribute] = {}
    for key, value in data.items():
        attr_key = f"agentkit.{key}"
        if isinstance(value, (str, bool, int, float)):
            attrs[attr_key] = value
        elif value is not None:
            attrs[attr_key] = json.dumps(value, ensure_ascii=False, default=str)
    return attrs
