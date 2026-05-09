"""Composable middleware pipeline for enterprise concerns.

Each middleware implements a single cross-cutting behavior (tenant isolation,
rate limiting, tracing, etc.) by wrapping the inner `run_turn()` call. The
chain processes events in onion-layers: outer middleware runs first, inner
middleware runs last.

Protocol:
    class Middleware(Protocol):
        async def run(
            self,
            ctx: MiddlewareContext,
            next_fn: Callable[[MiddlewareContext], Awaitable[str]],
        ) -> str:
            ...

``MiddlewareContext`` bundles all the primitives a middleware might need —
spec, context, query, and the original turn function reference — so layers
don't need to know about each other.

Usage::

    chain = MiddlewareChain([
        TenantIsolationMiddleware(),
        QuotaMiddleware(...),
        RetryMiddleware(...),
        TracingMiddleware(...),
    ])
    result = await chain.run(
        spec, context, query,
        inner=lambda spec, ctx, q: run_turn(...),
    )
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from agentengine.base.context import AgentContext
from agentengine.spec import AgentSpec

logger = logging.getLogger(__name__)

InnerTurnFn = Callable[
    [AgentSpec, AgentContext, str], Awaitable[str],
]

MiddlewareFn = Callable[
    ["MiddlewareContext", Callable[["MiddlewareContext"], Awaitable[str]]],
    Awaitable[str],
]


@dataclass
class MiddlewareContext:
    """Bundled primitives for one middleware invocation.

    Every middleware receives this context so it can inspect the agent spec,
    the run context, or inject extra data into ``extras``.
    """

    spec: AgentSpec
    run_context: AgentContext
    query: str
    extras: dict[str, Any]


class MiddlewareChain:
    """Compose a list of middleware into a single entry point."""

    def __init__(self, layers: Sequence[MiddlewareFn] = ()) -> None:
        self._layers = list(layers)

    def add(self, layer: MiddlewareFn) -> None:
        self._layers.append(layer)

    async def run(
        self,
        spec: AgentSpec,
        context: AgentContext,
        query: str,
        *,
        inner: InnerTurnFn,
    ) -> str:
        """Execute the full chain with an inner-most turn function."""

        async def _inner(mw_ctx: MiddlewareContext) -> str:
            return await inner(spec, mw_ctx.run_context, query)

        return await _compose(self._layers, _inner, spec, context, query)


async def _compose(
    layers: list[MiddlewareFn],
    inner: Callable[[MiddlewareContext], Awaitable[str]],
    spec: AgentSpec,
    context: AgentContext,
    query: str,
) -> str:
    """Build the nested call chain from layers."""

    async def _dispatch(
        index: int,
        mw_ctx: MiddlewareContext,
    ) -> str:
        if index >= len(layers):
            return await inner(mw_ctx)
        return await layers[index](mw_ctx, lambda ctx: _dispatch(index + 1, ctx))

    mw_ctx = MiddlewareContext(
        spec=spec,
        run_context=context,
        query=query,
        extras=context.extras,
    )
    return await _dispatch(0, mw_ctx)