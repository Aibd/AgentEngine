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

from agentkit.base.context import AgentContext
from agentkit.base.state import AgentState
from agentkit.memory.memory import Memory
from agentkit.spec import AgentSpec


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
    def max_steps(self) -> int:
        return self.spec.max_steps

    def system_prompt(self) -> str:
        return self.spec.system_prompt

    def next_step_prompt(self) -> str:
        return self.spec.next_step_prompt

    async def setup(self) -> None:
        if self.spec.setup is not None:
            await self.spec.setup(self.context)

    async def teardown(self) -> None:
        if self.spec.teardown is not None:
            await self.spec.teardown(self.context)
