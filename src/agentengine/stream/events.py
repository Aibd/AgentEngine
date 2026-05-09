from enum import Enum


class EventType(str, Enum):
    """Stream event types emitted via Printer.

    Stable string values matter: SSE v2 uses these as `event:` names.

    Runtime lifecycle events are defined in `agentengine.runtime.events`.
    Keep this module focused on the external stream protocol.
    """

    # Lifecycle
    START = "start"
    DONE = "done"

    # Per-turn boundaries
    STEP = "step"            # turn N started
    STEP_END = "step_end"    # turn N ended (with has_tool_calls flag)

    # Model output
    THINKING = "thinking"    # chain-of-thought / reasoning_content delta
    TEXT = "text"            # final answer delta

    # Tool lifecycle
    TOOL_CALL_START = "tool_call_start"  # tool invoked with arguments
    TOOL_RESULT = "tool_result"          # tool finished with result + ok flag

    # Telemetry
    USAGE = "usage"          # token / duration totals at run end

    # Compatibility events from streaming tools.
    TASK = "task"
    TOOL_THOUGHT = "tool_thought"
    SEARCH_RESULT = "search_result"
    FINAL_RESULT = "final_result"

    # Session-side-channel events emitted by builtin tools.
    TODOS_UPDATED = "todos_updated"            # TodoWriteTool
    USER_QUESTION_ASKED = "user_question_asked"  # AskUserQuestionTool

    # Terminal
    ERROR = "error"


# Backwards-compatible alias for callers still importing the old name.
AgentEventType = EventType
