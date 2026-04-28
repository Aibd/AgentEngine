from abc import ABC, abstractmethod

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext


class AgentHandler(ABC):
    name = "base"

    @abstractmethod
    async def handle(self, agent: BaseAgent, context: AgentContext, query: str) -> str:
        ...
