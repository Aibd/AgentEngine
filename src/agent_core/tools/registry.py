from threading import RLock
from typing import Any, Callable, TypeVar

from agent_core.tools.base import Tool

T = TypeVar("T", bound=type[Tool])
_TOOL_REGISTRY: dict[str, type[Tool]] = {}
_LOCK = RLock()


def register_tool(name: str | None = None) -> Callable[[T], T]:
    def decorator(cls: T) -> T:
        raw_name = name if name is not None else getattr(cls, "name", cls.__name__)
        tool_name = str(raw_name)
        with _LOCK:
            _TOOL_REGISTRY[tool_name] = cls
            cls.name = tool_name
        return cls

    return decorator


def create_tool(name: str, **kwargs: Any) -> Tool:
    with _LOCK:
        tool_cls = _TOOL_REGISTRY.get(name)
    if tool_cls is None:
        raise KeyError(f"Tool not registered: {name}")
    return tool_cls(**kwargs)


def registered_tools() -> dict[str, type[Tool]]:
    with _LOCK:
        return dict(_TOOL_REGISTRY)
