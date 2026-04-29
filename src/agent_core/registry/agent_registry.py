from threading import RLock
from typing import Any, Callable, TypeVar

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext

T = TypeVar("T", bound=type[BaseAgent])
_AGENT_REGISTRY: dict[str, type[BaseAgent]] = {}
_AGENT_HANDLERS: dict[str, str] = {}
_LOCK = RLock()


def register_agent(name: str, *, handler: str = "react") -> Callable[[T], T]:
    def decorator(cls: T) -> T:
        with _LOCK:
            _AGENT_REGISTRY[name] = cls
            _AGENT_HANDLERS[name] = handler
            cls.name = name
        return cls

    return decorator


def create_agent(name: str, context: AgentContext, **kwargs: Any) -> BaseAgent:
    with _LOCK:
        agent_cls = _AGENT_REGISTRY.get(name)
    if agent_cls is None:
        raise KeyError(f"Agent not registered: {name}")
    return agent_cls(context, **kwargs)


def get_agent_handler(name: str) -> str:
    with _LOCK:
        return _AGENT_HANDLERS.get(name, "react")


def registered_agents() -> dict[str, type[BaseAgent]]:
    with _LOCK:
        return dict(_AGENT_REGISTRY)
