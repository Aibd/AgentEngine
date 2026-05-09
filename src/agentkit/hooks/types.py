"""Hook event types and payload dataclasses.

Each hook event carries a frozen payload object describing what happened.
Handlers receive the payload, may inspect it, and return a :class:`HookResult`
indicating whether dispatch should continue or abort.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Union


class HookEvent(str, Enum):
    """The lifecycle points where hooks can intercept agent execution."""

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    STOP = "Stop"


class HookOutcome(str, Enum):
    """What a single handler decided.

    - ``SUCCESS``: handler ran cleanly, dispatch continues to the next handler
    - ``FAIL_CONTINUE``: handler reported a soft failure, dispatch continues
      (used for e.g. logging hooks that don't want to abort the run)
    - ``FAIL_ABORT``: handler wants the in-progress operation aborted; the
      dispatcher stops calling subsequent handlers and signals the caller
    """

    SUCCESS = "success"
    FAIL_CONTINUE = "fail_continue"
    FAIL_ABORT = "fail_abort"


@dataclass(frozen=True, slots=True)
class HookResult:
    """Single-handler result, mirrored after codex's ``HookResult`` enum."""

    outcome: HookOutcome
    reason: str = ""

    @classmethod
    def success(cls) -> "HookResult":
        return cls(outcome=HookOutcome.SUCCESS)

    @classmethod
    def fail_continue(cls, reason: str = "") -> "HookResult":
        return cls(outcome=HookOutcome.FAIL_CONTINUE, reason=reason)

    @classmethod
    def fail_abort(cls, reason: str = "") -> "HookResult":
        return cls(outcome=HookOutcome.FAIL_ABORT, reason=reason)

    @property
    def should_abort(self) -> bool:
        return self.outcome is HookOutcome.FAIL_ABORT


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class _BasePayload:
    """Common fields every hook payload carries."""

    session_id: str = ""
    run_id: str = ""
    turn_id: str = ""
    cwd: str = ""
    triggered_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class PreToolUsePayload(_BasePayload):
    """Fired just before a tool runs.

    A handler returning ``fail_abort`` causes the tool call to be skipped
    and a ``ToolCallFailed`` event emitted with the reason.
    """

    tool_name: str = ""
    tool_call_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PostToolUsePayload(_BasePayload):
    """Fired immediately after a tool finishes (success or failure)."""

    tool_name: str = ""
    tool_call_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    result_summary: str = ""
    error_type: str = ""
    error_message: str = ""
    elapsed_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class SessionStartPayload(_BasePayload):
    """Fired once at the start of a TurnRunner.run().

    Aborting here cancels the run before any LLM call.
    """

    agent_name: str = ""
    query_summary: str = ""


@dataclass(frozen=True, slots=True)
class UserPromptSubmitPayload(_BasePayload):
    """Fired when the user query is about to be added to memory.

    Aborting here cancels the run before any model invocation.
    """

    agent_name: str = ""
    query: str = ""


@dataclass(frozen=True, slots=True)
class StopPayload(_BasePayload):
    """Fired when a run is wrapping up (success, failure, or cancellation).

    The result of aborting here is undefined — by the time Stop fires, the
    operation is already concluding. Use it for cleanup (flushing logs,
    closing connections), not for control flow.
    """

    agent_name: str = ""
    status: str = "completed"  # completed | failed | cancelled
    elapsed_seconds: float = 0.0


HookPayload = Union[
    PreToolUsePayload,
    PostToolUsePayload,
    SessionStartPayload,
    UserPromptSubmitPayload,
    StopPayload,
]


# A hook handler is an async callable that receives a payload and returns a
# HookResult. We accept sync functions too — the manager wraps them.
HookFn = Callable[[HookPayload], Union[HookResult, Awaitable[HookResult], None, Awaitable[None]]]


class HookAbortError(RuntimeError):
    """Raised by the dispatcher when a handler returns ``fail_abort``.

    Caller code at the dispatch site catches this and converts it to whatever
    abort mechanism is appropriate (skip the tool, fail the run, etc.).
    """

    def __init__(self, event: HookEvent, handler_name: str, reason: str) -> None:
        super().__init__(
            f"hook aborted operation: event={event.value} "
            f"handler={handler_name} reason={reason!r}"
        )
        self.event = event
        self.handler_name = handler_name
        self.reason = reason
