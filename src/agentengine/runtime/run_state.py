from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TerminalReason(str, Enum):
    NORMAL = "normal"
    TOOL_FAILED = "tool_failed"
    MODEL_FAILED = "model_failed"
    CONTEXT_EXCEEDED = "context_exceeded"
    MAX_TURNS = "max_turns"
    RUNTIME_FAILED = "runtime_failed"
    QUOTA_EXCEEDED = "quota_exceeded"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class RunState:
    run_id: str
    session_id: str
    turn_id: str
    status: RunStatus = RunStatus.PENDING
    terminal_reason: TerminalReason | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime = field(default_factory=_utc_now)

    def mark_running(self) -> None:
        self.status = RunStatus.RUNNING
        self.started_at = _utc_now()
        self.ended_at = None
        self.terminal_reason = None

    def mark_completed(self, reason: TerminalReason = TerminalReason.NORMAL) -> None:
        self.status = RunStatus.COMPLETED
        self.terminal_reason = reason
        self.ended_at = _utc_now()

    def mark_failed(self, reason: TerminalReason) -> None:
        self.status = RunStatus.FAILED
        self.terminal_reason = reason
        self.ended_at = _utc_now()

    def mark_cancelled(self) -> None:
        self.status = RunStatus.CANCELLED
        self.terminal_reason = TerminalReason.CANCELLED
        self.ended_at = _utc_now()
