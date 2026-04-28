from collections.abc import Awaitable, Callable

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.handlers.base import AgentHandler
from agent_core.registry.handler_registry import register_handler

PipelineStep = Callable[[AgentContext, str], Awaitable[str]]


@register_handler("pipeline")
class PipelineHandler(AgentHandler):
    name = "pipeline"

    def __init__(self, steps: list[PipelineStep] | None = None) -> None:
        self.steps = steps or []

    async def handle(self, agent: BaseAgent, context: AgentContext, query: str) -> str:
        result = query
        for step in self.steps:
            result = await step(context, result)
        if not self.steps:
            result = await agent.run(query)
        return result
