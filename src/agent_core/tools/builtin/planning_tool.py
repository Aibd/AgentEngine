from dataclasses import dataclass, field
from typing import Any

from agent_core.tools.base import Tool
from agent_core.tools.registry import register_tool


@dataclass
class Plan:
    steps: list[str]
    status: list[str] = field(init=False)
    current_index: int = 0

    def __post_init__(self) -> None:
        self.status = ["pending" for _ in self.steps]

    @property
    def current_step(self) -> str:
        if self.current_index >= len(self.steps):
            return ""
        return self.steps[self.current_index]

    def advance(self) -> None:
        if self.current_index < len(self.status):
            self.status[self.current_index] = "completed"
        self.current_index += 1

    def is_finished(self) -> bool:
        return self.current_index >= len(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {"steps": self.steps, "status": self.status, "currentStep": self.current_step}


@register_tool("planning_tool")
class PlanningTool(Tool):
    description = "Create and advance an explicit task plan."
    schema = {
        "type": "object",
        "properties": {
            "steps": {"type": "array", "items": {"type": "string"}},
            "action": {"type": "string", "enum": ["create", "advance", "inspect"]},
        },
        "required": ["action"],
    }

    def __init__(self) -> None:
        self.plan: Plan | None = None

    async def run(self, *, action: str, steps: list[str] | None = None, **_: Any) -> dict[str, Any]:
        if action == "create":
            self.plan = Plan(steps or [])
            return {"created": True, "plan": self.plan.to_dict()}
        if action == "advance":
            if self.plan:
                self.plan.advance()
            return {"advanced": True, "plan": self.plan.to_dict() if self.plan else None}
        return {"plan": self.plan.to_dict() if self.plan else None}
