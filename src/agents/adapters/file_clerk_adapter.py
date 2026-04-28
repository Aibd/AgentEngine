import asyncio
from typing import Any, Callable

from agent_core.base.agent import BaseAgent
from agent_core.registry.agent_registry import register_agent


@register_agent("file_clerk", handler="pipeline")
class FileClerkAdapter(BaseAgent):
    """Adapter shell for the existing FileClerkAgent.

    Keep the legacy implementation outside agent_core. Inject a factory that
    returns the current FileClerkAgent instance. The adapter bridges its
    message_queue output into the new Printer event shape.
    """

    def __init__(self, context, *, legacy_factory: Callable[[asyncio.Queue], Any] | None = None, max_steps: int = 1) -> None:
        super().__init__(context, max_steps=max_steps)
        self.legacy_factory = legacy_factory

    async def think(self) -> bool:
        return self.current_step == 0

    async def act(self) -> str:
        if self.legacy_factory is None:
            return "file_clerk adapter ready; inject legacy_factory to run."

        queue: asyncio.Queue = asyncio.Queue()
        legacy = self.legacy_factory(queue)
        task = asyncio.create_task(legacy.run())
        while not task.done():
            try:
                message = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if self.context.printer:
                finished = message.get("status") == "end" if isinstance(message, dict) else False
                await self.context.printer.send("text", message, finished=finished)
        result = await task
        return str(result)
