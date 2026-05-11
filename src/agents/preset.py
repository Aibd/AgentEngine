"""Application-level agent presets.

Presets are an adapter layer: they can be declared however the application
prefers, then compiled into the core runtime's `RunConfig`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentengine.memory.message import Message
from agentengine.run_config import RunConfig, SetupHook


@dataclass(frozen=True, slots=True)
class AgentPreset:
    name: str
    description: str = ""
    instructions: str = ""
    max_turns: int | None = None
    max_steps: int | None = None
    max_messages: int = 0
    setup: SetupHook | None = None
    teardown: SetupHook | None = None
    extras: dict[str, object] = field(default_factory=dict)

    def to_run_config(self) -> RunConfig:
        initial_messages = (
            (Message.system(self.instructions),)
            if self.instructions
            else ()
        )
        return RunConfig(
            name=self.name,
            initial_messages=initial_messages,
            max_turns=self.max_turns,
            max_steps=self.max_steps,
            max_messages=self.max_messages,
            setup=self.setup,
            teardown=self.teardown,
            extras=dict(self.extras),
        )
