from typing import Any, Callable, TypeVar

from agent_core.tools.base import Tool

T = TypeVar("T", bound=type[Tool])
_TOOL_REGISTRY: dict[str, type[Tool]] = {}


def register_tool(name: str | None = None) -> Callable[[T], T]:
    def decorator(cls: T) -> T:
        raw_name = name if name is not None else getattr(cls, "name", cls.__name__)
        tool_name = str(raw_name)
        _TOOL_REGISTRY[tool_name] = cls
        cls.name = tool_name
        return cls

    return decorator


def create_tool(name: str, **kwargs: Any) -> Tool:
    if name not in _TOOL_REGISTRY:
        raise KeyError(f"Tool not registered: {name}")
    return _TOOL_REGISTRY[name](**kwargs)


def registered_tools() -> dict[str, type[Tool]]:
    return dict(_TOOL_REGISTRY)
