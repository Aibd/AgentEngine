"""HookManager: registry + sequential dispatcher.

Per-event handlers run in registration order. The first handler that returns
``fail_abort`` stops the chain and the dispatcher raises :class:`HookAbortError`
for the caller to handle. Exceptions from handlers are caught and converted to
``fail_continue`` (with the exception message preserved) — by default a buggy
hook can't take down the run.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from agentkit.hooks.types import (
    HookAbortError,
    HookEvent,
    HookFn,
    HookOutcome,
    HookPayload,
    HookResult,
)

logger = logging.getLogger(__name__)


class HookManager:
    """Registers and dispatches hooks for a single agent runtime.

    Thread-safe: registration and dispatch can interleave from different
    asyncio tasks. The lock is only held during list mutation; handlers
    themselves run outside the lock so they can do anything async.
    """

    def __init__(self) -> None:
        self._handlers: dict[HookEvent, list[tuple[str, HookFn]]] = {
            event: [] for event in HookEvent
        }
        self._lock = threading.RLock()

    # -- Registration --------------------------------------------------------

    def register(
        self,
        event: HookEvent,
        handler: HookFn,
        *,
        name: str | None = None,
    ) -> None:
        """Append a handler for ``event``.

        ``name`` defaults to the handler's ``__name__`` and is shown in logs
        and abort errors. Anonymous lambdas should pass an explicit name.
        """
        handler_name: str = name or str(getattr(handler, "__name__", "anonymous"))
        with self._lock:
            self._handlers[event].append((handler_name, handler))
        logger.debug("hook_registered event=%s name=%s", event.value, handler_name)

    def on(
        self,
        event: HookEvent,
        *,
        name: str | None = None,
    ) -> Callable[[HookFn], HookFn]:
        """Decorator form of :meth:`register`."""

        def _decorator(fn: HookFn) -> HookFn:
            self.register(event, fn, name=name)
            return fn

        return _decorator

    def unregister(self, event: HookEvent, handler: HookFn) -> bool:
        """Remove a previously registered handler. Returns True if found."""
        with self._lock:
            handlers = self._handlers[event]
            for index, (_, fn) in enumerate(handlers):
                if fn is handler:
                    handlers.pop(index)
                    return True
        return False

    def clear(self, event: HookEvent | None = None) -> None:
        """Drop all handlers for ``event`` (or all events when None)."""
        with self._lock:
            if event is None:
                for key in self._handlers:
                    self._handlers[key].clear()
            else:
                self._handlers[event].clear()

    def count(self, event: HookEvent) -> int:
        with self._lock:
            return len(self._handlers[event])

    # -- Dispatch ------------------------------------------------------------

    async def dispatch(self, event: HookEvent, payload: HookPayload) -> list[HookResult]:
        """Run every handler for ``event`` in order; raise on abort.

        Returns the list of results from handlers that ran (handlers after
        an abort don't run, so the list is shorter than ``count(event)`` in
        that case — though the abort case actually raises before the list
        is returned to the caller).
        """
        with self._lock:
            handlers = list(self._handlers[event])
        if not handlers:
            return []

        results: list[HookResult] = []
        for handler_name, fn in handlers:
            result = await _invoke(fn, payload, handler_name=handler_name, event=event)
            results.append(result)
            if result.outcome is HookOutcome.FAIL_ABORT:
                logger.warning(
                    "hook_aborted event=%s handler=%s reason=%s",
                    event.value,
                    handler_name,
                    result.reason,
                )
                raise HookAbortError(event, handler_name, result.reason)
        return results

    # -- Snapshot for diagnostics -------------------------------------------

    def snapshot(self) -> dict[str, list[str]]:
        """Return a {event: [handler_name, ...]} dict; useful for tests."""
        with self._lock:
            return {
                event.value: [name for name, _ in handlers]
                for event, handlers in self._handlers.items()
            }


async def _invoke(
    fn: HookFn,
    payload: HookPayload,
    *,
    handler_name: str,
    event: HookEvent,
) -> HookResult:
    try:
        outcome = fn(payload)
        if inspect.isawaitable(outcome):
            outcome = await outcome
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — defensive boundary
        logger.exception(
            "hook_handler_exception event=%s handler=%s",
            event.value,
            handler_name,
        )
        return HookResult.fail_continue(reason=f"{type(exc).__name__}: {exc}")
    if outcome is None:
        return HookResult.success()
    if isinstance(outcome, HookResult):
        return outcome
    logger.warning(
        "hook_handler_unexpected_return event=%s handler=%s value=%r",
        event.value,
        handler_name,
        outcome,
    )
    return HookResult.success()


# Module-level default manager. Most callers won't touch this — TurnRunner
# threads its own manager through ``context.extras["hooks"]``. It exists so
# that single-process applications can attach hooks before the runtime is
# constructed (e.g. at import time) by reaching for a stable handle.
_DEFAULT_LOCK = threading.Lock()
_default_manager: HookManager | None = None


def get_default_manager() -> HookManager:
    """Return a process-wide HookManager, creating it on first call."""
    global _default_manager
    with _DEFAULT_LOCK:
        if _default_manager is None:
            _default_manager = HookManager()
        return _default_manager


@contextmanager
def temporary_default_manager(manager: HookManager) -> Iterator[HookManager]:
    """Test helper: swap the default manager for the duration of a block."""
    global _default_manager
    with _DEFAULT_LOCK:
        previous = _default_manager
        _default_manager = manager
    try:
        yield manager
    finally:
        with _DEFAULT_LOCK:
            _default_manager = previous


def _has_handlers(manager: Any, event: HookEvent) -> bool:
    """Polymorphic helper used by dispatch sites to skip no-op work."""
    if manager is None:
        return False
    counter = getattr(manager, "count", None)
    if callable(counter):
        try:
            return int(counter(event)) > 0
        except Exception:  # noqa: BLE001
            return True
    return True
