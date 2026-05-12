"""Runtime configuration for a single agent loop.

`RunConfig` is intentionally about execution mechanics, not agent identity
declaration. Callers provide the initial messages, turn limits, and optional
lifecycle hooks; the runtime only knows how to run the loop.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agentengine.base.context import AgentContext
from agentengine.memory.message import Message
from agentengine.runtime.compaction import Compactor

SetupHook = Callable[[AgentContext], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Immutable configuration shared by one or more runs."""

    name: str
    initial_messages: tuple[Message, ...] = ()
    max_messages: int = 0
    auto_compact_tokens: int = 0
    compaction_keep_recent: int = 8
    compactor: Compactor | None = None
    setup: SetupHook | None = None
    teardown: SetupHook | None = None
    extras: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("RunConfig.name must not be empty")
        if self.max_messages < 0:
            raise ValueError("RunConfig.max_messages must be at least 0")
        if self.auto_compact_tokens < 0:
            raise ValueError("RunConfig.auto_compact_tokens must be at least 0")
        if self.compaction_keep_recent < 0:
            raise ValueError("RunConfig.compaction_keep_recent must be at least 0")
