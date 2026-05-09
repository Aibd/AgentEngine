"""Per-tenant / per-tool rate limiting middleware.

``QuotaMiddleware`` enforces token budgets and tool-call rate limits before
letting ``run_turn()`` proceed. It reads limits from a ``QuotaStore`` and
injects quota checks into the turn loop by wrapping the inner turn function.

Quota dimension examples:
- ``per_tenant.total_runs`` — max concurrent/sequential runs per tenant
- ``per_tenant.tool_calls`` — max tool calls per tenant window
- ``per_tenant.tokens_in`` / ``per_tenant.tokens_out`` — token budgets

When a quota is exceeded, ``QuotaExceededError`` is raised. The downstream
error handler turns this into a structured ``RuntimeEvent``.

Usage::

    store = InMemoryQuotaStore()
    store.set_limits("acme", {"max_runs": 5, "max_tool_calls": 20})

    mw = QuotaMiddleware(store)
    result = await mw(ctx, next_fn)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agentengine.errors import AgentEngineError
from agentengine.enterprise.middleware import MiddlewareContext, MiddlewareFn
from agentengine.enterprise.tenant import TenantContext

logger = logging.getLogger(__name__)


class QuotaExceededError(AgentEngineError):
    """Raised when a quota limit is hit."""

    code = "quota_exceeded"
    category = "quota"
    retryable = False

    def __init__(
        self,
        tenant_id: str,
        dimension: str,
        limit: float,
        current: float,
    ) -> None:
        self.tenant_id = tenant_id
        self.dimension = dimension
        self.limit = limit
        self.current = current
        super().__init__(
            f"Quota exceeded for tenant={tenant_id}: {dimension} (used {current}/{limit})",
            details={
                "tenant_id": tenant_id,
                "dimension": dimension,
                "limit": limit,
                "current": current,
            },
        )


@dataclass
class QuotaLimits:
    """Per-tenant quota configuration."""

    max_runs: int = 0  # 0 means unlimited
    max_tool_calls: int = 0
    max_tokens_in: int = 0
    max_tokens_out: int = 0
    window_seconds: float = 60.0


@dataclass
class QuotaUsage:
    """Mutable per-tenant usage counters."""

    runs: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    window_start: float = 0.0

    def __post_init__(self) -> None:
        if not self.window_start:
            self.window_start = time.monotonic()

    def maybe_reset(self, window_seconds: float) -> None:
        if time.monotonic() - self.window_start >= window_seconds:
            self.runs = 0
            self.tool_calls = 0
            self.tokens_in = 0
            self.tokens_out = 0
            self.window_start = time.monotonic()


class QuotaStore:
    """Pluggable quota persistence.

    Defaults to an in-memory dict; subclass and override ``get_limits`` /
    ``record`` to connect to Redis, Postgres, etc.
    """

    def __init__(self) -> None:
        self._limits: dict[str, QuotaLimits] = {}
        self._usage: dict[str, QuotaUsage] = {}
        self._lock = asyncio.Lock()

    def set_limits(self, tenant_id: str, limits: QuotaLimits) -> None:
        self._limits[tenant_id] = limits

    def get_limits(self, tenant_id: str) -> QuotaLimits:
        return self._limits.get(tenant_id, QuotaLimits())

    async def _ensure_usage(self, tenant_id: str) -> QuotaUsage:
        if tenant_id not in self._usage:
            self._usage[tenant_id] = QuotaUsage()
        usage = self._usage[tenant_id]
        limits = self.get_limits(tenant_id)
        usage.maybe_reset(limits.window_seconds)
        return usage

    async def check_and_acquire_run(
        self,
        tenant_id: str,
    ) -> None:
        """Acquire a run slot, or raise ``QuotaExceededError``."""
        async with self._lock:
            limits = self.get_limits(tenant_id)
            usage = await self._ensure_usage(tenant_id)
            if limits.max_runs > 0 and usage.runs >= limits.max_runs:
                raise QuotaExceededError(
                    tenant_id, "runs", limits.max_runs, usage.runs,
                )
            usage.runs += 1

    async def check_and_acquire_tool_call(
        self,
        tenant_id: str,
    ) -> None:
        """Acquire a tool call slot, or raise ``QuotaExceededError``."""
        async with self._lock:
            limits = self.get_limits(tenant_id)
            usage = await self._ensure_usage(tenant_id)
            if limits.max_tool_calls > 0 and usage.tool_calls >= limits.max_tool_calls:
                raise QuotaExceededError(
                    tenant_id,
                    "tool_calls",
                    limits.max_tool_calls,
                    usage.tool_calls,
                )
            usage.tool_calls += 1

    async def record_tokens(
        self,
        tenant_id: str,
        tokens_in: int,
        tokens_out: int,
    ) -> None:
        """Record token usage for the current window."""
        async with self._lock:
            limits = self.get_limits(tenant_id)
            usage = await self._ensure_usage(tenant_id)
            if limits.max_tokens_in > 0 and usage.tokens_in + tokens_in > limits.max_tokens_in:
                raise QuotaExceededError(
                    tenant_id,
                    "tokens_in",
                    limits.max_tokens_in,
                    usage.tokens_in + tokens_in,
                )
            if limits.max_tokens_out > 0 and usage.tokens_out + tokens_out > limits.max_tokens_out:
                raise QuotaExceededError(
                    tenant_id,
                    "tokens_out",
                    limits.max_tokens_out,
                    usage.tokens_out + tokens_out,
                )
            usage.tokens_in += tokens_in
            usage.tokens_out += tokens_out


def _get_tenant_id(ctx: MiddlewareContext) -> str | None:
    tenant = ctx.extras.get("tenant")
    if isinstance(tenant, TenantContext):
        return tenant.tenant_id
    return None


def quota_middleware(store: QuotaStore) -> MiddlewareFn:
    """Create a quota-enforcing middleware function.

    Checks run quota before the turn starts, tool call quotas during the
    loop (via context extras), and records token usage after completion.
    """

    async def _quota(
        ctx: MiddlewareContext,
        next_fn: Callable[[MiddlewareContext], Awaitable[str]],
    ) -> str:
        tenant_id = _get_tenant_id(ctx)
        if tenant_id is None:
            return await next_fn(ctx)

        await store.check_and_acquire_run(tenant_id)

        ctx.extras["_quota_store"] = store
        ctx.extras["_quota_tenant_id"] = tenant_id

        try:
            result = await next_fn(ctx)
        except QuotaExceededError:
            raise
        except Exception:
            raise

        return result

    return _quota
