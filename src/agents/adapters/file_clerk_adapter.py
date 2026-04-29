"""Adapter shell for the existing FileClerkAgent.

The legacy implementation is kept outside agent_core. Inject a factory that
returns the current FileClerkAgent instance bound to an asyncio.Queue. This
adapter bridges the legacy queue output into the new Printer event shape.

Registered with handler "pipeline" because FileClerk is a fixed workflow,
not a ReAct loop. The adapter exposes a pipeline step that runs the legacy
agent and bridges its queue messages into the new Printer event shape.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.registry.agent_registry import register_agent

PipelineStep = Callable[[AgentContext, str], Awaitable[str]]


@register_agent("file_clerk", handler="pipeline")
class FileClerkAdapter(BaseAgent):
    def __init__(
        self,
        context: AgentContext,
        *,
        legacy_factory: Callable[[asyncio.Queue[Any]], Any] | None = None,
        max_steps: int = 1,
    ) -> None:
        super().__init__(context, max_steps=max_steps)
        self.legacy_factory = legacy_factory

    def setup(self) -> None:
        # Keep the factory discoverable for application code that still reads
        # legacy integration metadata from the context.
        if self.legacy_factory is not None:
            self.context.extras.setdefault("file_clerk_factory", self.legacy_factory)

    def pipeline_steps(self) -> list[PipelineStep]:
        return [self._run_legacy_step]

    async def _run_legacy_step(self, context: AgentContext, query: str) -> str:
        return await self.run_legacy(query)

    async def run_legacy(self, query: str) -> str:
        """Run the legacy agent and bridge its message_queue to Printer."""
        if self.legacy_factory is None:
            return "file_clerk adapter ready; inject legacy_factory to run."

        queue: asyncio.Queue[Any] = asyncio.Queue()
        legacy = self.legacy_factory(queue)
        task = asyncio.create_task(legacy.run())
        try:
            while not task.done() or not queue.empty():
                try:
                    if task.done():
                        message = queue.get_nowait()
                    else:
                        message = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.QueueEmpty:
                    break
                except asyncio.TimeoutError:
                    continue
                if self.context.printer:
                    finished = (
                        message.get("status") == "end"
                        if isinstance(message, dict)
                        else False
                    )
                    await self.context.printer.send("text", message, finished=finished)
            result = await task
            return str(result) if result is not None else ""
        except asyncio.CancelledError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            raise
        except Exception:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            raise
