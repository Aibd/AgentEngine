"""Enterprise middleware primitives for Agent Core.

Each module delivers one cross-cutting concern as an optional, composable
layer around `run_turn()`. None of them are required — the framework runs
fine without this package.

Capabilities:
  - tenant       TenantContext — multi-tenant id scopes
  - quota        QuotaMiddleware — per-tenant / per-tool rate limiting
  - approval     ApprovalGate — human-in-the-loop for destructive tools
  - tracing      OTelTracer — OpenTelemetry span hierarchy
  - secrets      SecretsProvider — abstract secret backends
  - retry        RetryMiddleware — exponential-backoff retry policies
  - middleware   MiddlewareChain — composable pipeline

Usage::

    from agent_core.enterprise.middleware import MiddlewareChain
    from agent_core.enterprise.tenant import TenantContext

    chain = MiddlewareChain([
        tracing_middleware,
        quota_middleware,
        retry_middleware,
        approval_middleware,
    ])
    result = await chain.run(spec, context, query, inner_turn_fn)
"""

from agent_core.enterprise.approval import (
    ApprovalDecision,
    ApprovalDeniedError,
    ApprovalGate,
    ApprovalResult,
    approval_middleware,
)
from agent_core.enterprise.middleware import (
    MiddlewareChain,
    MiddlewareContext,
    MiddlewareFn,
)
from agent_core.enterprise.quota import (
    QuotaExceededError,
    QuotaLimits,
    QuotaStore,
    QuotaUsage,
    quota_middleware,
)
from agent_core.enterprise.retry import RetryConfig, retry_middleware
from agent_core.enterprise.secrets import EnvSecrets, SecretsError, SecretsProvider
from agent_core.enterprise.tenant import TenantContext
from agent_core.enterprise.tracing import (
    get_current_span,
    otel_tracing_middleware,
    span_context,
)

__all__ = [
    # Tenant
    "TenantContext",
    # Secrets
    "EnvSecrets",
    "SecretsError",
    "SecretsProvider",
    # Middleware infrastructure
    "MiddlewareChain",
    "MiddlewareContext",
    "MiddlewareFn",
    # Quota
    "QuotaExceededError",
    "QuotaLimits",
    "QuotaStore",
    "QuotaUsage",
    "quota_middleware",
    # Approval
    "ApprovalDecision",
    "ApprovalDeniedError",
    "ApprovalGate",
    "ApprovalResult",
    "approval_middleware",
    # OTel tracing
    "get_current_span",
    "otel_tracing_middleware",
    "span_context",
    # Retry
    "RetryConfig",
    "retry_middleware",
]