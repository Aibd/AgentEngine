"""Application presets used by the demo CLI and web API."""

from agents.deep_research.preset import PRESET as DEEP_RESEARCH
from agents.general_chat.preset import PRESET as GENERAL_CHAT
from agents.preset import AgentPreset

REGISTRY: dict[str, AgentPreset] = {
    GENERAL_CHAT.name: GENERAL_CHAT,
    DEEP_RESEARCH.name: DEEP_RESEARCH,
}

__all__ = ["AgentPreset", "DEEP_RESEARCH", "GENERAL_CHAT", "REGISTRY"]
