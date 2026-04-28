from typing import Callable, TypeVar

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext

T = TypeVar("T", bound=type[BaseAgent])
_AGENT_REGISTRY: dict[str, type[BaseAgent]] = {}
_AGENT_HANDLERS: dict[str, str] = {}


def register_agent(name: str, *, handler: str = "react") -> Callable[[T], T]:
    def decorator(cls: T) -> T:
        _AGENT_REGISTRY[name] = cls
        _AGENT_HANDLERS[name] = handler
        cls.name = name
        return cls
    return decorator


def create_agent(name: str, context: AgentContext, **kwargs) -> BaseAgent:
    if name not in _AGENT_REGISTRY:
        raise KeyError(f"Agent not registered: {name}")
    return _AGENT_REGISTRY[name](context, **kwargs)


def get_agent_handler(name: str) -> str:
    return _AGENT_HANDLERS.get(name, "react")


def registered_agents() -> dict[str, type[BaseAgent]]:
    return dict(_AGENT_REGISTRY)
