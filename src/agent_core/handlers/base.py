from abc import ABC, abstractmethod

from agent_core.base.agent import AgentRun
from agent_core.base.context import AgentContext


class AgentHandler(ABC):
    name = "base"

    @abstractmethod
    async def handle(self, agent: AgentRun, context: AgentContext, query: str) -> str:
        ...
