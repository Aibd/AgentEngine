from typing import Any, Callable

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.handlers.base import AgentHandler
from agent_core.registry.handler_registry import register_handler


@register_handler("legacy")
class LegacyHandler(AgentHandler):
    name = "legacy"

    def __init__(self, factory: Callable[[AgentContext], Any] | None = None) -> None:
        self.factory = factory

    async def handle(self, agent: BaseAgent, context: AgentContext, query: str) -> str:
        if self.factory is None:
            return await agent.run(query)
        legacy = self.factory(context)
        result = legacy.run(query)
        if hasattr(result, "__await__"):
            result = await result
        return result
