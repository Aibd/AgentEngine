"""Per-run mutable state for an agent invocation.

`AgentRun` is a plain data container for the runtime state of one execution:
memory, step counter, and lifecycle state. Reusable execution settings live
on `RunConfig`; higher layers can build that config however they like.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from agentengine.base.context import AgentContext
from agentengine.base.state import AgentState
from agentengine.memory.memory import Memory
from agentengine.run_config import RunConfig


@dataclass(slots=True)
class AgentRun:
    config: RunConfig
    context: AgentContext
    memory: Memory = field(init=False)
    current_step: int = 0
    state: AgentState = AgentState.IDLE

    def __post_init__(self) -> None:
        self.memory = Memory(max_messages=self.config.max_messages)

    @property
    def name(self) -> str:
        return self.config.name

    async def setup(self) -> None:
        # Initial messages are supplied by the caller. The runtime does not
        # assign special meaning to system prompts or agent declarations.
        self.memory.extend(deepcopy(self.config.initial_messages))
        if self.config.setup is not None:
            await self.config.setup(self.context)

    async def teardown(self) -> None:
        if self.config.teardown is not None:
            await self.config.teardown(self.context)
