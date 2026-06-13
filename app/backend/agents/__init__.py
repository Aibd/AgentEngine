"""Example agent definitions used by the demo CLI and web API.

Definitions are declared as Markdown files (YAML frontmatter + body) under
``markdown/`` and loaded at import time. This mirrors the Claude Code subagent
format — see :mod:`agentengine.definition_loader`.
"""

from pathlib import Path

from agentengine.definition import AgentDefinition
from agentengine.definition_loader import load_definitions

_DEFINITION_DIR = Path(__file__).parent / "markdown"

REGISTRY: dict[str, AgentDefinition] = load_definitions(_DEFINITION_DIR)

# Backward-compatible named handles for callers that imported these directly.
GENERAL_CHAT = REGISTRY["general_chat"]
DEEP_RESEARCH = REGISTRY["deep_research"]

__all__ = ["AgentDefinition", "DEEP_RESEARCH", "GENERAL_CHAT", "REGISTRY"]
