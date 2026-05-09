"""Built-in agent registry.

Each `AgentSpec` is declared in its own module's `spec.py` and registered
explicitly here. No decorators, no import side effects beyond the imports
on this line — `agents.REGISTRY[name]` is the single source of truth.
"""

from agentkit.spec import AgentSpec
from agents.deep_research.spec import SPEC as DEEP_RESEARCH
from agents.general_chat.spec import SPEC as GENERAL_CHAT

REGISTRY: dict[str, AgentSpec] = {
    GENERAL_CHAT.name: GENERAL_CHAT,
    DEEP_RESEARCH.name: DEEP_RESEARCH,
}

__all__ = ["DEEP_RESEARCH", "GENERAL_CHAT", "REGISTRY"]
