from dataclasses import dataclass, field
from typing import Any

from agent_core.llm.client import LLMClient
from agent_core.stream.printer import Printer
from agent_core.tools.collection import ToolCollection


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
    extras: dict[str, Any] = field(default_factory=dict)
