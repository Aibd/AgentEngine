from agent_core.base.agent import BaseAgent
from agent_core.registry.agent_registry import register_agent
from agent_core.tools.builtin.planning_tool import PlanningTool


@register_agent("deep_research", handler="react")
class DeepResearchAgent(BaseAgent):
    description = "Research agent using ReAct and explicit planning/tool calls."

    def __init__(self, context, *, max_steps: int = 10) -> None:
        super().__init__(context, max_steps=max_steps)
        if context.tool_collection.get("planning_tool") is None:
            context.tool_collection.add(PlanningTool())

    async def think(self) -> bool:
        return self.current_step == 0

    async def act(self) -> str:
        if self.context.llm is None:
            return "deep_research mock response"
        response = await self.context.llm.chat(
            self.memory.messages,
            tools=self.context.tool_collection.to_openai_tools(),
        )
        return response.content
