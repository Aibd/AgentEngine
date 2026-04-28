from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    name: str
    description: str = ""
    schema: dict[str, Any] = {"type": "object", "properties": {}}

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
