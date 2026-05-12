"""OpenTelemetry tracing middleware.

Wraps every ``run_turn()`` in an OTel span so operators get distributed traces
across agent runs. Each LLM call and tool execution is recorded as a child
span with typed attributes (agent name, model, tool args, token counts).

Dependencies:
    pip install opentelemetry-api opentelemetry-sdk

The sink is optional: when OTel is not installed, the middleware silently
passes through. This matches the existing ``OTelSink`` philosophy.

Usage::

    from agentengine.enterprise.tracing import otel_tracing_middleware

    chain = MiddlewareChain([otel_tracing_middleware()])
"""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from agentengine.enterprise.middleware import MiddlewareContext, MiddlewareFn

logger = logging.getLogger(__name__)

_current_span: ContextVar[Any] = ContextVar("otel_current_span", default=None)


def _get_trace_module() -> Any | None:
    try:
        return importlib.import_module("opentelemetry.trace")
    except ModuleNotFoundError:
        return None


def _get_status_module() -> Any | None:
    try:
        return importlib.import_module("opentelemetry.trace.status")
    except ModuleNotFoundError:
        return None


def _get_tracer() -> Any | None:
    trace = _get_trace_module()
    if trace is None:
        return None
    return trace.get_tracer("agentengine.enterprise")


def otel_tracing_middleware() -> MiddlewareFn:
    """Create an OTel tracing middleware.

    Creates a root span for the agent run, sets attributes from the spec,
    and injects the span into a contextvar so downstream code (LLM calls,
    tool exec) can create child spans.
    """

    async def _trace(
        ctx: MiddlewareContext,
        next_fn: Callable[[MiddlewareContext], Awaitable[str]],
    ) -> str:
        tracer = _get_tracer()
        if tracer is None:
            return await next_fn(ctx)

        trace = _get_trace_module()
        status_module = _get_status_module()

        span_name = f"agent_run.{ctx.config.name}"
        agent_span = tracer.start_as_current_span(span_name)
        span = agent_span.__enter__()
        _current_span.set(span)

        span.set_attribute("agentengine.agent_name", ctx.config.name)
        span.set_attribute("agentengine.loop_limit", "unbounded")
        span.set_attribute("agentengine.query_length", len(ctx.query))

        started_at = time.perf_counter()
        try:
            result = await next_fn(ctx)
            span.set_attribute("agentengine.elapsed_seconds", time.perf_counter() - started_at)
            if status_module:
                span.set_status(status_module.Status(status_module.StatusCode.OK))
            return result
        except Exception as exc:
            span.set_attribute("agentengine.elapsed_seconds", time.perf_counter() - started_at)
            span.set_attribute("agentengine.error", str(exc))
            span.set_attribute("agentengine.error_type", type(exc).__name__)
            if status_module:
                span.set_status(
                    status_module.Status(status_module.StatusCode.ERROR, str(exc))
                )
            span.record_exception(exc)
            raise
        finally:
            agent_span.__exit__(None, None, None)
            _current_span.set(None)

    return _trace


def get_current_span() -> Any:
    """Return the current OTel span from contextvar, or None."""
    return _current_span.get()


@contextmanager
def span_context(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any | None]:
    """Create a child span within the current agent run span.

    Usage::

        with span_context("llm_call", {"model": "gpt-4o"}) as span:
            # do LLM work
    """
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return

    parent = _current_span.get()
    span = tracer.start_span(name)
    if attributes:
        for k, v in attributes.items():
            try:
                span.set_attribute(k, v)
            except Exception:
                span.set_attribute(k, str(v))
    token = _current_span.set(span)
    try:
        yield span
    except Exception as exc:
        span.record_exception(exc)
        raise
    finally:
        _current_span.reset(token)
        span.end()
