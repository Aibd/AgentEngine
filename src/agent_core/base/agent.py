from abc import ABC, abstractmethod

from agent_core.base.context import AgentContext
from agent_core.base.state import AgentState
from agent_core.memory.memory import Memory
from agent_core.memory.message import Message


class BaseAgent(ABC):
    name = "base"
    description = ""

    def __init__(self, context: AgentContext, *, max_steps: int = 10) -> None:
        self.context = context
        self.memory = Memory()
        self.max_steps = max_steps
        self.current_step = 0
        self.state = AgentState.IDLE

    async def run(self, query: str | None = None) -> str:
        self.state = AgentState.RUNNING
        if query:
            self.memory.append(Message.user(query))
        result = ""
        while self.state == AgentState.RUNNING and self.current_step < self.max_steps:
            should_act = await self.think()
            if not should_act:
                self.state = AgentState.FINISHED
                break
            result = await self.act()
            self.current_step += 1
        return await self.finalize(result)

    @abstractmethod
    async def think(self) -> bool:
        ...

    @abstractmethod
    async def act(self) -> str:
        ...

    async def finalize(self, result: str) -> str:
        self.state = AgentState.FINISHED if self.state != AgentState.ERROR else self.state
        return result
