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

SetupHook = Callable[[AgentContext], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Immutable configuration shared by one or more runs."""

    name: str
    initial_messages: tuple[Message, ...] = ()
    max_turns: int | None = None
    max_steps: int | None = None
    max_messages: int = 0
    setup: SetupHook | None = None
    teardown: SetupHook | None = None
    extras: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("RunConfig.name must not be empty")
        if self.max_turns is not None and self.max_turns < 1:
            raise ValueError("RunConfig.max_turns must be at least 1")
        if self.max_steps is not None and self.max_steps < 1:
            raise ValueError("RunConfig.max_steps must be at least 1")
        if (
            self.max_turns is not None
            and self.max_steps is not None
            and self.max_turns != self.max_steps
        ):
            raise ValueError("RunConfig.max_turns and max_steps must match when both are set")
        if self.max_messages < 0:
            raise ValueError("RunConfig.max_messages must be at least 0")

    @property
    def effective_max_turns(self) -> int | None:
        """Runtime turn limit, with deprecated ``max_steps`` as a compat alias."""
        return self.max_turns if self.max_turns is not None else self.max_steps
