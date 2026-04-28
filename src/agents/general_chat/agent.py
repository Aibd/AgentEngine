from agent_core.base.agent import BaseAgent
from agent_core.registry.agent_registry import register_agent


@register_agent("general_chat", handler="react")
class GeneralChatAgent(BaseAgent):
    description = "Simple chat agent with no special business workflow."

    async def think(self) -> bool:
        return self.current_step == 0

    async def act(self) -> str:
        if self.context.llm is None:
            return "general_chat mock response"
        response = await self.context.llm.chat(self.memory.messages)
        return response.content
