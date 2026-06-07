"""Example presets used by the demo CLI and web API.

Presets are declared as Markdown files (YAML frontmatter + body) under
``markdown/`` and loaded at import time. This mirrors the Claude Code subagent
format — see :mod:`agentengine.preset_loader`.
"""

from pathlib import Path

from agentengine.preset import AgentPreset
from agentengine.preset_loader import load_presets

_PRESET_DIR = Path(__file__).parent / "markdown"

REGISTRY: dict[str, AgentPreset] = load_presets(_PRESET_DIR)

# Backward-compatible named handles for callers that imported these directly.
GENERAL_CHAT = REGISTRY["general_chat"]
DEEP_RESEARCH = REGISTRY["deep_research"]

__all__ = ["AgentPreset", "DEEP_RESEARCH", "GENERAL_CHAT", "REGISTRY"]
