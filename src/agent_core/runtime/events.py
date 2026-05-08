from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _serialize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    if isinstance(value, tuple):
        return [_serialize_value(v) for v in value]
    return value


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Base class for semantic runtime events.

    This module models agent runtime events. The SSE protocol event names live
    in `agent_core.stream.events.EventType`; these layers must stay separate.
    """

    run_id: str
    turn_id: str
    timestamp: datetime = field(default_factory=_utc_now)

    event_type: ClassVar[str] = "runtime_event"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"event_type": self.event_type}
        for item in fields(self):
            data[item.name] = _serialize_value(getattr(self, item.name))
        return data


@dataclass(frozen=True, slots=True)
class RunStarted(RuntimeEvent):
    agent_name: str = ""
    input_summary: str = ""

    event_type: ClassVar[str] = "run_started"


@dataclass(frozen=True, slots=True)
class TextDelta(RuntimeEvent):
    content: str = ""

    event_type: ClassVar[str] = "text_delta"


@dataclass(frozen=True, slots=True)
class ReasoningDelta(RuntimeEvent):
    """Streaming chunk of model chain-of-thought / reasoning_content."""

    content: str = ""

    event_type: ClassVar[str] = "reasoning_delta"


@dataclass(frozen=True, slots=True)
class TurnStarted(RuntimeEvent):
    """Boundary marker emitted at the start of each ReAct iteration."""

    turn: int = 0

    event_type: ClassVar[str] = "turn_started"


@dataclass(frozen=True, slots=True)
class TurnEnded(RuntimeEvent):
    """Boundary marker emitted when a ReAct iteration completes."""

    turn: int = 0
    has_tool_calls: bool = False
    elapsed_seconds: float = 0.0

    event_type: ClassVar[str] = "turn_ended"


@dataclass(frozen=True, slots=True)
class UsageReport(RuntimeEvent):
    """Token usage and duration summary, typically emitted before run end."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    total_seconds: float = 0.0

    event_type: ClassVar[str] = "usage_report"


@dataclass(frozen=True, slots=True)
class ToolCallStarted(RuntimeEvent):
    tool_call_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)

    event_type: ClassVar[str] = "tool_call_started"


@dataclass(frozen=True, slots=True)
class ToolStreamEventEmitted(RuntimeEvent):
    """Intermediate event from a StreamingTool.

    `stream_event_type` names the downstream stream protocol event, while this
    object remains the semantic runtime event that sinks consume.
    """

    tool_call_id: str = ""
    tool_name: str = ""
    stream_event_type: str = ""
    data: Any = None
    is_final: bool = False

    event_type: ClassVar[str] = "tool_stream_event"


@dataclass(frozen=True, slots=True)
class ToolCallCompleted(RuntimeEvent):
    tool_call_id: str = ""
    tool_name: str = ""
    result_summary: str = ""
    elapsed_seconds: float = 0.0

    event_type: ClassVar[str] = "tool_call_completed"


@dataclass(frozen=True, slots=True)
class ToolCallFailed(RuntimeEvent):
    tool_call_id: str = ""
    tool_name: str = ""
    error_type: str = ""
    error_message: str = ""
    elapsed_seconds: float = 0.0

    event_type: ClassVar[str] = "tool_call_failed"


@dataclass(frozen=True, slots=True)
class RunCompleted(RuntimeEvent):
    result_summary: str = ""
    elapsed_seconds: float = 0.0

    event_type: ClassVar[str] = "run_completed"


@dataclass(frozen=True, slots=True)
class RunFailed(RuntimeEvent):
    error_type: str = ""
    error_message: str = ""
    terminal_reason: str = "runtime_failed"
    elapsed_seconds: float = 0.0
    # Structured error payload from `error_to_dict()` if available - carries
    # code / category / retryable / details for richer SSE rendering.
    error_payload: dict[str, Any] | None = None

    event_type: ClassVar[str] = "run_failed"


@dataclass(frozen=True, slots=True)
class RunCancelled(RuntimeEvent):
    reason: str = "cancelled"
    elapsed_seconds: float = 0.0

    event_type: ClassVar[str] = "run_cancelled"


@dataclass(frozen=True, slots=True)
class ApprovalRequired(RuntimeEvent):
    """Emitted when a destructive tool needs human approval."""
    approval_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | approved | denied
    reason: str = ""
    event_type: ClassVar[str] = "approval_required"


@dataclass(frozen=True, slots=True)
class TodosUpdated(RuntimeEvent):
    """Emitted whenever ``TodoWriteTool`` rewrites the session task list.

    The frontend can subscribe to this to render a live checklist; runs that
    don't use TodoWriteTool will simply never see this event.
    """

    todos: list[dict[str, Any]] = field(default_factory=list)
    event_type: ClassVar[str] = "todos_updated"


@dataclass(frozen=True, slots=True)
class UserQuestionAsked(RuntimeEvent):
    """Emitted when the agent asks the user a clarifying question mid-run.

    The tool returns immediately with a placeholder marker; the actual
    answer arrives as the next user message in a subsequent turn (the
    conversation_id keeps state consistent). The frontend renders this as
    a prompt block so the user knows to respond.
    """

    question_id: str = ""
    question: str = ""
    options: list[str] = field(default_factory=list)
    multiple: bool = False
    event_type: ClassVar[str] = "user_question_asked"
