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
        if 0 <= self.current_index < len(self.steps):
            return self.steps[self.current_index]
        return ""

    def advance(self) -> None:
        if self.current_index < len(self.status):
            self.status[self.current_index] = "completed"
        self.current_index += 1

    def is_finished(self) -> bool:
        return bool(self.steps) and all(s == "completed" for s in self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "status": self.status,
            "currentIndex": self.current_index,
            "currentStep": self.current_step,
            "isFinished": self.is_finished(),
        }


@register_tool("planning_tool")
class PlanningTool(Tool):
    description = (
        "Create and manage a step-by-step task plan. "
        "Use action='create' with steps=[...] to start a plan, "
        "action='advance' to mark current step done, "
        "action='inspect' to read plan state."
    )
    schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "advance", "inspect"],
                "description": "Operation to perform on the plan",
            },
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Required for action='create'",
            },
        },
        "required": ["action"],
    }

    def __init__(self) -> None:
        self.plan: Plan | None = None

    async def run(self, *, action: str, steps: list[str] | None = None, **_: Any) -> dict[str, Any]:
        if action == "create":
            self.plan = Plan(steps=list(steps) if steps else [])
            return {"created": True, "plan": self.plan.to_dict()}
        if action == "advance":
            if self.plan is None:
                return {"error": "No plan exists. Call action='create' first."}
            self.plan.advance()
            return {"advanced": True, "plan": self.plan.to_dict()}
        # inspect (default)
        if self.plan is None:
            return {"plan": None}
        return {"plan": self.plan.to_dict()}
