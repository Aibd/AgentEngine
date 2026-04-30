from abc import ABC, abstractmethod
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
