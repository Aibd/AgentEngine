"""Declarative agent specification.

`AgentSpec` is the data-driven replacement for the old `BaseAgent` class
hierarchy. An agent is no longer a class to subclass — it is a frozen
dataclass instance describing **what the agent is** (name, prompts, limits,
optional setup/teardown callbacks). The think→act loop is the same for
every agent; differences are expressed as data, not subtypes.

This mirrors the design of codex's `AgentRoleConfig` and claude-code's
`AgentDefinition` + `QueryParams` bundle: a single loop, many specs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agentengine.base.context import AgentContext

SetupHook = Callable[[AgentContext], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Immutable description of an agent variant.

    Fields are all declarative — they describe identity and configuration,
    never runtime state. Per-run mutable state (memory, step counter, agent
    status) lives in `AgentRun` and is created fresh for each invocation.
    """

    name: str
    system_prompt: str = ""
    next_step_prompt: str = ""
    description: str = ""
    max_steps: int = 10
    max_messages: int = 0
    setup: SetupHook | None = None
    teardown: SetupHook | None = None
    extras: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("AgentSpec.name must not be empty")
        if self.max_steps < 1:
            raise ValueError("AgentSpec.max_steps must be at least 1")
        if self.max_messages < 0:
            raise ValueError("AgentSpec.max_messages must be at least 0")
