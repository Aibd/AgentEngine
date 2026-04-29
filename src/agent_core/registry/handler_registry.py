from threading import RLock
from typing import Any, Callable, TypeVar

T = TypeVar("T", bound=type[Any])
_HANDLER_REGISTRY: dict[str, type[Any]] = {}
_LOCK = RLock()


def register_handler(name: str) -> Callable[[T], T]:
    def decorator(cls: T) -> T:
        with _LOCK:
            _HANDLER_REGISTRY[name] = cls
            cls.name = name
        return cls

    return decorator


def create_handler(name: str, **kwargs: Any) -> Any:
    with _LOCK:
        handler_cls = _HANDLER_REGISTRY.get(name)
    if handler_cls is None:
        raise KeyError(f"Handler not registered: {name}")
    return handler_cls(**kwargs)


def registered_handlers() -> dict[str, type[Any]]:
    with _LOCK:
        return dict(_HANDLER_REGISTRY)
