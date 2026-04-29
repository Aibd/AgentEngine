"""Agent context — auto-registers SkillTool so every agent gets skill support."""
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

    def __post_init__(self) -> None:
        """Ensure every agent context has the Skill tool available."""
        from agent_core.skills.loader import SkillLoader
        from agent_core.tools.builtin.skill_tool import SkillTool
        if not any(t.name == "Skill" for t in self.tool_collection.tool_map.values()):
            loader = self.extras.get("skill_loader") or SkillLoader()
            self.tool_collection.add(SkillTool(loader))
