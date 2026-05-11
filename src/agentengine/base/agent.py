"""Per-run mutable state for an agent invocation.

`AgentRun` replaces the old `BaseAgent` class hierarchy. It is no longer
something you subclass — it is a plain data container for the *runtime*
state of one agent execution: memory, step counter, lifecycle state.

Identity, prompts, and limits live on `AgentSpec` (frozen, run-invariant).
The think→act loop receives both: spec for what to do, run for what is
happening right now.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentengine.base.context import AgentContext
from agentengine.base.state import AgentState
from agentengine.memory.memory import Memory
from agentengine.spec import AgentSpec


@dataclass(slots=True)
class AgentRun:
    spec: AgentSpec
    context: AgentContext
    memory: Memory = field(init=False)
    current_step: int = 0
    state: AgentState = AgentState.IDLE

    def __post_init__(self) -> None:
        self.memory = Memory(max_messages=self.spec.max_messages)

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def max_turns(self) -> int | None:
        return self.spec.effective_max_turns

    @property
    def max_steps(self) -> int | None:
        return self.max_turns

    def system_prompt(self) -> str:
        return self.spec.system_prompt

    async def setup(self) -> None:
        # System prompt is injected before the user prompt hook fires so
        # downstream handlers (and memory hydration from persistence) see a
        # consistent ordering. Memory.load_from_db preserves leading system
        # messages, so we won't duplicate it on resumed conversations.
        if self.spec.system_prompt:
            self.memory.add_system_message(self.spec.system_prompt)
        if self.spec.setup is not None:
            await self.spec.setup(self.context)

    async def teardown(self) -> None:
        if self.spec.teardown is not None:
            await self.spec.teardown(self.context)
