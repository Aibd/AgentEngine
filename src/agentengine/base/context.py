"""Mutable per-run context passed from the host application into the engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentengine.llm.client import LLMClient
from agentengine.stream.printer import Printer
from agentengine.tools.collection import ToolCollection

if TYPE_CHECKING:
    from agentengine.persistence.port import PersistencePort


@dataclass
class AgentContext:
    request_id: str
    query: str
    llm: LLMClient | None = None
    printer: Printer | None = None
    tool_collection: ToolCollection = field(default_factory=ToolCollection)
    session_id: str = ""
    conversation_id: str = ""
    user: Any = None
    db: Any = None
    persistence: PersistencePort | None = None
    extras: dict[str, Any] = field(default_factory=dict)
