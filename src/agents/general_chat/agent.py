from agent_core.base.agent import BaseAgent
from agent_core.registry.agent_registry import register_agent


@register_agent("general_chat", handler="react")
class GeneralChatAgent(BaseAgent):
    """Plain chat agent. No tools, single LLM round-trip via ReActHandler."""

    description = "Simple chat agent with no special business workflow."

    def system_prompt(self) -> str:
        return "You are a helpful assistant. Answer the user directly."
