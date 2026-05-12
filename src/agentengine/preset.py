"""Public agent preset declaration.

Presets are a small SDK-facing adapter layer: application code can describe an
agent in business-friendly terms, then compile it into the runtime's
``RunConfig`` before execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentengine.memory.message import Message
from agentengine.run_config import RunConfig, SetupHook
from agentengine.runtime.compaction import Compactor


DEFAULT_AGENT_SYSTEM_PROMPT = (
    "Keep going until the user's request is completely resolved before ending "
    "your turn. Only stop when you are confident the work is done. If you are "
    "not sure, continue reasoning, use the available tools, or ask a concise "
    "clarifying question."
)


@dataclass(frozen=True, slots=True)
class AgentPreset:
    """Declarative agent configuration for host applications."""

    name: str
    description: str = ""
    instructions: str = ""
    max_messages: int = 0
    auto_compact_tokens: int = 0
    compaction_keep_recent: int = 8
    compactor: Compactor | None = None
    setup: SetupHook | None = None
    teardown: SetupHook | None = None
    extras: dict[str, object] = field(default_factory=dict)

    def to_run_config(self) -> RunConfig:
        system_prompt = _compose_system_prompt(self.instructions)
        initial_messages = (Message.system(system_prompt),)
        return RunConfig(
            name=self.name,
            initial_messages=initial_messages,
            max_messages=self.max_messages,
            auto_compact_tokens=self.auto_compact_tokens,
            compaction_keep_recent=self.compaction_keep_recent,
            compactor=self.compactor,
            setup=self.setup,
            teardown=self.teardown,
            extras=dict(self.extras),
        )


def _compose_system_prompt(instructions: str) -> str:
    instructions = instructions.strip()
    if instructions:
        return f"{instructions}\n\n{DEFAULT_AGENT_SYSTEM_PROMPT}"
    return DEFAULT_AGENT_SYSTEM_PROMPT


__all__ = ["AgentPreset", "DEFAULT_AGENT_SYSTEM_PROMPT"]
