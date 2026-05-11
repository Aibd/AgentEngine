"""Redis-backed conversation lock reference implementation.

This module intentionally does not import the ``redis`` package. Host
applications pass a compatible async Redis client, for example
``redis.asyncio.Redis``.
"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
end
return 0
"""


class RedisConversationLockManager:
    """Distributed conversation lock using Redis ``SET NX PX``.

    The lock is scoped by ``conversation_id``. Empty ids are ignored, matching
    ``InMemoryConversationLockManager``.
    """

    def __init__(
        self,
        redis_client: Any,
        *,
        key_prefix: str = "agentengine:conversation-lock:",
        ttl_seconds: float = 30.0,
        retry_interval_seconds: float = 0.1,
        acquire_timeout_seconds: float | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than 0")
        if retry_interval_seconds <= 0:
            raise ValueError("retry_interval_seconds must be greater than 0")
        if acquire_timeout_seconds is not None and acquire_timeout_seconds <= 0:
            raise ValueError("acquire_timeout_seconds must be greater than 0")
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._ttl_ms = max(1, int(ttl_seconds * 1000))
        self._retry_interval = retry_interval_seconds
        self._acquire_timeout = acquire_timeout_seconds

    @asynccontextmanager
    async def acquire(self, conversation_id: str) -> AsyncIterator[None]:
        if not conversation_id:
            yield
            return

        key = f"{self._key_prefix}{conversation_id}"
        token = uuid.uuid4().hex
        await self._acquire_key(key, token)
        try:
            yield
        finally:
            await self._release_key(key, token)

    async def _acquire_key(self, key: str, token: str) -> None:
        started_at = time.monotonic()
        while True:
            ok = await _maybe_await(
                self._redis.set(key, token, nx=True, px=self._ttl_ms)
            )
            if ok:
                return
            if (
                self._acquire_timeout is not None
                and time.monotonic() - started_at >= self._acquire_timeout
            ):
                raise TimeoutError(f"Timed out acquiring Redis lock: {key}")
            await asyncio.sleep(self._retry_interval)

    async def _release_key(self, key: str, token: str) -> None:
        eval_fn = getattr(self._redis, "eval", None)
        if eval_fn is not None:
            await _maybe_await(eval_fn(_RELEASE_SCRIPT, 1, key, token))
            return

        current = await _maybe_await(self._redis.get(key))
        if _decode(current) == token:
            await _maybe_await(self._redis.delete(key))


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


__all__ = ["RedisConversationLockManager"]
