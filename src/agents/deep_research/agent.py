from agent_core.base.agent import BaseAgent
from agent_core.registry.agent_registry import register_agent
from agent_core.tools.builtin.planning_tool import PlanningTool


@register_agent("deep_research", handler="react")
class DeepResearchAgent(BaseAgent):
    """Research agent that uses ReAct with explicit planning + tool calls."""

    description = "Research agent using ReAct and explicit planning/tool calls."

    def setup(self) -> None:
        if self.context.tool_collection.get("planning_tool") is None:
            self.context.tool_collection.add(PlanningTool())

    def system_prompt(self) -> str:
        return (
            "You are a deep research agent. "
            "Use the planning_tool to break complex tasks into ordered steps, "
            "call tools when useful, and produce a grounded final report."
        )

    def next_step_prompt(self) -> str:
        return "Think step-by-step. If a plan exists, advance it. Stop when done."
