from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Literal


class Tool(ABC):
    name: str
    description: str = ""
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    timeout_seconds: float = 30.0
    is_destructive: bool = False
    max_result_chars: int = 8000
    result_summary_strategy: Literal["truncate", "head_tail", "none"] = "truncate"

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }

    @abstractmethod
    async def run(self, **kwargs: Any) -> Any:
        ...


@dataclass(slots=True)
class ToolStreamEvent:
    """A single intermediate event emitted by a ``StreamingTool``.

    ``event_type`` maps to a downstream stream protocol event name (e.g.
    ``"tool_thought"``, ``"search_result"``, ``"final_result"``). ``data`` is
    wrapped in a runtime event before any sink renders it. When ``is_final`` is
    True the executor
    treats ``data`` as the tool's final result.
    """

    event_type: str = "tool_thought"
    data: Any = ""
    is_final: bool = False


class StreamingTool(Tool):
    """Base class for tools that emit intermediate events (e.g. deep search).

    Subclasses implement ``run_stream()`` as an async generator that yields
    ``ToolStreamEvent`` objects. The executor consumes these events, emits
    intermediate runtime events, and uses the final event's ``data`` as the
    tool result.

    The default ``run()`` implementation collects all non-final events into
    a single string so that callers who treat the tool as a plain ``Tool``
    still get a usable result.
    """

    async def run_stream(
        self, **kwargs: Any
    ) -> AsyncGenerator[ToolStreamEvent, None]:
        """Override in subclasses to yield intermediate events.

        The generator **must** yield at least one event with ``is_final=True``
        to provide the tool result.  If it doesn't, the executor will
        synthesise a result from accumulated non-final data.
        """
        # Default: delegate to run() and emit a single final event.
        result = await self.run(**kwargs)
        yield ToolStreamEvent(event_type="result", data=str(result), is_final=True)

    async def run(self, **kwargs: Any) -> Any:
        """Default plain-run: consume run_stream and concatenate."""
        parts: list[str] = []
        async for event in self.run_stream(**kwargs):
            if not event.is_final:
                parts.append(str(event.data))
            else:
                parts.append(str(event.data))
                break
        return "\n".join(parts)
