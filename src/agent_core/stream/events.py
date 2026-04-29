from enum import Enum


class EventType(str, Enum):
    """Stream event types emitted via Printer.

    Stable string values matter — the existing SSE contract uses these as
    `responseType` keys consumed by the front end.
    """

    START = "start"
    TEXT = "text"
    TASK = "task"
    TOOL_THOUGHT = "tool_thought"
    TOOL_RESULT = "tool_result"
    SEARCH_RESULT = "search_result"
    RESULT = "result"
    FINAL_RESULT = "final_result"
    ERROR = "error"
    DONE = "done"


# Backwards-compatible alias for callers still importing the old name.
AgentEventType = EventType
