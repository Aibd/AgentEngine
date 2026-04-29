from collections.abc import Awaitable, Callable

from agent_core.base.context import AgentContext
from agent_core.base.state import AgentState
from agent_core.memory.memory import Memory

PipelineStep = Callable[[AgentContext, str], Awaitable[str]]


class BaseAgent:
    """Pure context container for an agent run.

    Loop logic lives in handlers (ReActHandler, PipelineHandler, ...).
    Subclasses customise per-agent behaviour by overriding `setup()` and
    `system_prompt()` hooks; they no longer implement think/act loops.
    """

    name: str = "base"
    description: str = ""

    def __init__(
        self,
        context: AgentContext,
        *,
        max_steps: int = 10,
        max_messages: int = 0,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if max_messages < 0:
            raise ValueError("max_messages must be at least 0")
        self.context = context
        self.memory = Memory(max_messages=max_messages)
        self.max_steps = max_steps
        self.current_step = 0
        self.state = AgentState.IDLE

    def setup(self) -> None:
        """Called once by the handler before the loop starts.

        Override to register tools, seed memory, or inject extras into
        the context. Default is a no-op.
        """

    async def teardown(self) -> None:
        """Called once by the handler after the run exits.

        Override to release per-run resources created in `setup()` or during
        execution. Default is a no-op.
        """

    def system_prompt(self) -> str:
        """Return the system prompt for this agent. Empty disables it."""
        return ""

    def next_step_prompt(self) -> str:
        """Optional guidance injected before the last user message each turn."""
        return ""

    def pipeline_steps(self) -> list[PipelineStep]:
        """Optional fixed workflow steps for PipelineHandler."""
        return []
