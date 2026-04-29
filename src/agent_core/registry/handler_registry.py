from typing import Any, Callable, TypeVar

T = TypeVar("T", bound=type[Any])
_HANDLER_REGISTRY: dict[str, type[Any]] = {}


def register_handler(name: str) -> Callable[[T], T]:
    def decorator(cls: T) -> T:
        _HANDLER_REGISTRY[name] = cls
        cls.name = name
        return cls

    return decorator


def create_handler(name: str, **kwargs: Any) -> Any:
    if name not in _HANDLER_REGISTRY:
        raise KeyError(f"Handler not registered: {name}")
    return _HANDLER_REGISTRY[name](**kwargs)


def registered_handlers() -> dict[str, type[Any]]:
    return dict(_HANDLER_REGISTRY)
