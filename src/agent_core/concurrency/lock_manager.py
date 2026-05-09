"""Per-conversation locking.

Single-process MVP: a dict of asyncio.Locks keyed by conversation_id, with
opportunistic GC of unused entries. This serializes the memory load/save
cycle so two concurrent requests for the same conversation can't lose
turns to each other.

Multi-replica production deployments must replace ``InMemoryConversationLockManager``
with a Redis (SETNX + expiry) or Postgres advisory-lock implementation. The
``ConversationLockManager`` Protocol exists so callers don't bind to the
in-memory implementation.

Usage::

    manager = InMemoryConversationLockManager()
    async with manager.acquire("conv-1"):
        # Critical section: load memory → run turn → save memory.
        ...
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncContextManager, AsyncIterator, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ConversationLockManager(Protocol):
    """Acquire a mutual-exclusion lock scoped to a conversation."""

    def acquire(self, conversation_id: str) -> AsyncContextManager[None]:
        """Return an async context manager that holds the conversation lock."""
        ...


class InMemoryConversationLockManager:
    """Default lock manager: in-process asyncio.Lock per conversation_id.

    Locks are created lazily and garbage-collected once no waiter and no
    holder remains, so a long-lived service doesn't accumulate stale locks
    after each conversation finishes.
    """

    def __init__(self) -> None:
        # _locks: conversation_id -> Lock
        self._locks: dict[str, asyncio.Lock] = {}
        # _refcounts: number of pending acquires + holder; 0 → eligible for GC
        self._refcounts: dict[str, int] = {}
        # Master lock guarding the two dicts above.
        self._meta_lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, conversation_id: str) -> AsyncIterator[None]:
        """Hold the lock for ``conversation_id`` for the duration of the block.

        Empty conversation ids are not locked — without an id there's no
        shared state to protect, and forcing a lock there would serialize
        unrelated runs.
        """
        if not conversation_id:
            yield
            return

        lock = await self._checkout(conversation_id)
        try:
            await lock.acquire()
            try:
                yield
            finally:
                lock.release()
        finally:
            await self._checkin(conversation_id)

    async def _checkout(self, conversation_id: str) -> asyncio.Lock:
        async with self._meta_lock:
            lock = self._locks.get(conversation_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[conversation_id] = lock
                self._refcounts[conversation_id] = 0
            self._refcounts[conversation_id] += 1
            return lock

    async def _checkin(self, conversation_id: str) -> None:
        async with self._meta_lock:
            count = self._refcounts.get(conversation_id, 0) - 1
            if count <= 0:
                self._locks.pop(conversation_id, None)
                self._refcounts.pop(conversation_id, None)
                logger.debug("conversation_lock_released id=%s", conversation_id)
            else:
                self._refcounts[conversation_id] = count

    @property
    def active_lock_count(self) -> int:
        """Number of currently-tracked conversation locks (mostly for tests)."""
        return len(self._locks)
