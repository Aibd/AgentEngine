"""Exponential-backoff retry middleware for LLM errors.

Wraps the turn function with retry logic for transient LLM failures:
502/503/429 rate limits, timeouts, connection errors. Non-retryable errors
(context window exceeded, tool failures) propagate immediately.

Configuration lives on ``AgentSpec.extras`` or can be passed inline.

Usage::

    from agent_core.enterprise.retry import RetryConfig, retry_middleware

    config = RetryConfig(max_retries=3, base_delay=1.0, max_delay=30.0)
    chain = MiddlewareChain([retry_middleware(config)])
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent_core.enterprise.middleware import MiddlewareContext, MiddlewareFn
from agent_core.errors import (
    AgentCoreError,
    AgentCancelledError,
    LLMConnectionError,
    LLMContextWindowError,
    LLMHTTPError,
    LLMRateLimitError,
    LLMStreamError,
    LLMTimeoutError,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RetryConfig:
    """Retry policy configuration.

    ``max_retries=0`` disables retry. ``base_delay`` doubles on each attempt.
    ``retryable_codes`` are checked in the error payload for structured
    HTTP errors.
    """

    max_retries: int = 2
    base_delay: float = 1.0
    max_delay: float = 30.0
    retryable_codes: frozenset[str] = frozenset({
        "llm_rate_limited",
        "llm_timeout",
        "llm_connection_error",
        "llm_stream_error",
    })


_RETRYABLE_ERROR_TYPES = (
    LLMRateLimitError,
    LLMTimeoutError,
    LLMConnectionError,
    LLMStreamError,
    # LLMHTTPError with 502/503 are retryable
)


def _is_retryable(error: BaseException, config: RetryConfig) -> bool:
    if isinstance(error, asyncio.CancelledError):
        return False
    if isinstance(error, AgentCancelledError):
        return False
    if isinstance(error, LLMContextWindowError):
        return False

    if isinstance(error, _RETRYABLE_ERROR_TYPES):
        return True

    if isinstance(error, AgentCoreError):
        if error.error_code in config.retryable_codes:
            return True

    if isinstance(error, OSError):
        return True

    return False


def _delay(attempt: int, config: RetryConfig) -> float:
    delay_seconds = config.base_delay * (2 ** (attempt - 1))
    return float(min(delay_seconds, config.max_delay))


def retry_middleware(config: RetryConfig | None = None) -> MiddlewareFn:
    """Create a retry middleware function.

    Retries the entire turn on transient LLM errors with exponential backoff.
    """
    cfg = config or RetryConfig()

    async def _retry(
        ctx: MiddlewareContext,
        next_fn: Callable[[MiddlewareContext], Awaitable[str]],
    ) -> str:
        last_error: BaseException | None = None

        for attempt in range(cfg.max_retries + 1):
            try:
                return await next_fn(ctx)
            except Exception as exc:
                last_error = exc
                if not _is_retryable(exc, cfg):
                    raise
                if attempt >= cfg.max_retries:
                    logger.warning(
                        "retry_exhausted attempt=%d/%d error=%s",
                        attempt, cfg.max_retries, type(exc).__name__,
                    )
                    raise

                wait = _delay(attempt + 1, cfg)
                logger.info(
                    "retry_attempt attempt=%d/%d delay=%.1fs error=%s",
                    attempt + 1, cfg.max_retries, wait, type(exc).__name__,
                )
                await asyncio.sleep(wait)

        assert last_error is not None
        raise last_error

    return _retry
