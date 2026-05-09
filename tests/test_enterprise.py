"""Tests for enterprise middleware primitives."""

from __future__ import annotations

import asyncio

import pytest

from agentengine.base.context import AgentContext
from agentengine.enterprise import (
    ApprovalDeniedError,
    ApprovalGate,
    ApprovalResult,
    MiddlewareChain,
    MiddlewareContext,
    QuotaExceededError,
    QuotaLimits,
    QuotaStore,
    QuotaUsage,
    RetryConfig,
    TenantContext,
    approval_middleware,
    quota_middleware,
    retry_middleware,
)
from agentengine.errors import (
    LLMRateLimitError,
    LLMTimeoutError,
    LLMContextWindowError,
    ToolExecutionError,
)
from agentengine.spec import AgentSpec


# ---------------------------------------------------------------------------
# TenantContext
# ---------------------------------------------------------------------------

class TestTenantContext:
    def test_basic_identity(self):
        t = TenantContext(tenant_id="acme", user_id="u1")
        assert t.tenant_id == "acme"
        assert t.identity == "acme:u1"

    def test_identity_no_user(self):
        t = TenantContext(tenant_id="acme")
        assert t.identity == "acme"

    def test_has_scope_exact_match(self):
        t = TenantContext(tenant_id="acme", scopes=["tools:read", "models:gpt-4o"])
        assert t.has_scope("tools:read")
        assert not t.has_scope("tools:write")

    def test_has_scope_wildcard(self):
        t = TenantContext(tenant_id="acme", scopes=["tools:*"])
        assert t.has_scope("tools:read")
        assert t.has_scope("tools:write")
        assert not t.has_scope("models:gpt-4o")

    def test_has_scope_empty(self):
        t = TenantContext(tenant_id="acme")
        assert not t.has_scope("anything")


# ---------------------------------------------------------------------------
# QuotaStore & middleware
# ---------------------------------------------------------------------------

class TestQuotaStore:
    def test_acquire_run_under_limit(self):
        store = QuotaStore()
        store.set_limits("acme", QuotaLimits(max_runs=5))
        for _ in range(5):
            asyncio.run(store.check_and_acquire_run("acme"))

    def test_acquire_run_exceeds_limit(self):
        store = QuotaStore()
        store.set_limits("acme", QuotaLimits(max_runs=1))
        asyncio.run(store.check_and_acquire_run("acme"))
        with pytest.raises(QuotaExceededError):
            asyncio.run(store.check_and_acquire_run("acme"))

    def test_acquire_tool_call_exceeds_limit(self):
        store = QuotaStore()
        store.set_limits("acme", QuotaLimits(max_tool_calls=2))
        for _ in range(2):
            asyncio.run(store.check_and_acquire_tool_call("acme"))
        with pytest.raises(QuotaExceededError):
            asyncio.run(store.check_and_acquire_tool_call("acme"))

    def test_unlimited_by_default(self):
        store = QuotaStore()
        for _ in range(100):
            asyncio.run(store.check_and_acquire_run("acme"))

    def test_record_tokens_tracks_usage(self):
        store = QuotaStore()
        store.set_limits("acme", QuotaLimits(max_tokens_in=100))
        asyncio.run(store.record_tokens("acme", 50, 30))
        asyncio.run(store.record_tokens("acme", 50, 30))
        with pytest.raises(QuotaExceededError):
            asyncio.run(store.record_tokens("acme", 1, 0))


class TestQuotaMiddleWare:
    async def test_passes_when_quota_ok(self):
        store = QuotaStore()
        store.set_limits("acme", QuotaLimits(max_runs=5))
        mw = quota_middleware(store)
        spec = AgentSpec(name="test")
        ctx = MiddlewareContext(
            spec=spec,
            run_context=AgentContext(request_id="r1", query="q"),
            query="q",
            extras={"tenant": TenantContext(tenant_id="acme")},
        )

        called = False

        async def inner(c):
            nonlocal called
            called = True
            return "ok"

        result = await mw(ctx, inner)
        assert called
        assert result == "ok"

    async def test_blocks_when_quota_exceeded(self):
        store = QuotaStore()
        store.set_limits("acme", QuotaLimits(max_runs=1))
        # Pre-acquire the only allowed run
        await store.check_and_acquire_run("acme")
        mw = quota_middleware(store)
        spec = AgentSpec(name="test")
        ctx = MiddlewareContext(
            spec=spec,
            run_context=AgentContext(request_id="r1", query="q"),
            query="q",
            extras={"tenant": TenantContext(tenant_id="acme")},
        )

        async def inner(c):
            return "should not run"

        with pytest.raises(QuotaExceededError):
            await mw(ctx, inner)

    async def test_passes_without_tenant(self):
        store = QuotaStore()
        mw = quota_middleware(store)
        spec = AgentSpec(name="test")
        ctx = MiddlewareContext(
            spec=spec,
            run_context=AgentContext(request_id="r1", query="q"),
            query="q",
            extras={},
        )

        async def inner(c):
            return "no tenant"

        result = await mw(ctx, inner)
        assert result == "no tenant"


# ---------------------------------------------------------------------------
# ApprovalGate
# ---------------------------------------------------------------------------

class TestApprovalGate:
    async def test_approve_tool(self):
        gate = ApprovalGate(timeout_seconds=5.0)

        async def _run():
            # Fire off a concurrent resolver
            async def resolve_soon():
                await asyncio.sleep(0.02)
                ids = gate.pending_ids()
                if ids:
                    gate.resolve(ids[0], ApprovalResult(approved=True))

            asyncio.ensure_future(resolve_soon())
            result = await gate.request_approval("rm", {"path": "/tmp"})
            assert result.approved

        await _run()

    async def test_deny_tool(self):
        gate = ApprovalGate(timeout_seconds=5.0)

        async def _run():
            async def deny_soon():
                await asyncio.sleep(0.02)
                ids = gate.pending_ids()
                if ids:
                    gate.resolve(ids[0], ApprovalResult(approved=False, reason="unsafe"))

            asyncio.ensure_future(deny_soon())
            with pytest.raises(ApprovalDeniedError, match="unsafe"):
                await gate.request_approval("rm", {"path": "/"})

        await _run()

    async def test_timeout_triggers_deny(self):
        gate = ApprovalGate(timeout_seconds=0.05)
        with pytest.raises(ApprovalDeniedError, match="timed out"):
            await gate.request_approval("rm", {"path": "/"})

    async def test_resolve_unknown_id_returns_false(self):
        gate = ApprovalGate()
        assert not gate.resolve("nonexistent", ApprovalResult(approved=True))

    def test_pending_count(self):
        gate = ApprovalGate()
        assert gate.pending_count == 0

    async def test_approval_middleware_injects_gate(self):
        gate = ApprovalGate()
        mw = approval_middleware(gate)
        spec = AgentSpec(name="test")
        ctx = MiddlewareContext(
            spec=spec,
            run_context=AgentContext(request_id="r1", query="q"),
            query="q",
            extras={},
        )

        async def inner(c):
            assert c.run_context.extras.get("approval_gate") is gate
            return "ok"

        result = await mw(ctx, inner)
        assert result == "ok"


# ---------------------------------------------------------------------------
# Retry middleware
# ---------------------------------------------------------------------------

class TestRetryMiddleware:
    async def test_retries_transient_llm_errors(self):
        config = RetryConfig(max_retries=2, base_delay=0.01)
        mw = retry_middleware(config)
        spec = AgentSpec(name="test")
        ctx = MiddlewareContext(
            spec=spec,
            run_context=AgentContext(request_id="r1", query="q"),
            query="q",
            extras={},
        )

        attempts = 0

        async def inner(c):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise LLMRateLimitError("rate limited")
            return "ok"

        result = await mw(ctx, inner)
        assert result == "ok"
        assert attempts == 3

    async def test_does_not_retry_non_retryable(self):
        config = RetryConfig(max_retries=3, base_delay=0.01)
        mw = retry_middleware(config)
        spec = AgentSpec(name="test")
        ctx = MiddlewareContext(
            spec=spec,
            run_context=AgentContext(request_id="r1", query="q"),
            query="q",
            extras={},
        )

        attempts = 0

        async def inner(c):
            nonlocal attempts
            attempts += 1
            raise LLMContextWindowError("context too large")

        with pytest.raises(LLMContextWindowError):
            await mw(ctx, inner)
        assert attempts == 1

    async def test_exhausts_retries(self):
        config = RetryConfig(max_retries=2, base_delay=0.01)
        mw = retry_middleware(config)
        spec = AgentSpec(name="test")
        ctx = MiddlewareContext(
            spec=spec,
            run_context=AgentContext(request_id="r1", query="q"),
            query="q",
            extras={},
        )

        async def inner(c):
            raise LLMTimeoutError("timeout")

        with pytest.raises(LLMTimeoutError):
            await mw(ctx, inner)

    async def test_passes_success_on_first_try(self):
        config = RetryConfig(max_retries=3, base_delay=0.01)
        mw = retry_middleware(config)
        spec = AgentSpec(name="test")
        ctx = MiddlewareContext(
            spec=spec,
            run_context=AgentContext(request_id="r1", query="q"),
            query="q",
            extras={},
        )

        async def inner(c):
            return "first try"

        result = await mw(ctx, inner)
        assert result == "first try"


# ---------------------------------------------------------------------------
# MiddlewareChain composition
# ---------------------------------------------------------------------------

class TestMiddlewareChain:
    async def test_empty_chain_passes_through(self):
        chain = MiddlewareChain()
        spec = AgentSpec(name="test")
        context = AgentContext(request_id="r1", query="q")

        async def inner(s, c, q):
            return "direct"

        result = await chain.run(spec, context, "q", inner=inner)
        assert result == "direct"

    async def test_multi_layer_executes_in_order(self):
        order: list[str] = []

        async def mw1(ctx, next_fn):
            order.append("mw1_before")
            result = await next_fn(ctx)
            order.append("mw1_after")
            return result

        async def mw2(ctx, next_fn):
            order.append("mw2_before")
            result = await next_fn(ctx)
            order.append("mw2_after")
            return result

        chain = MiddlewareChain([mw1, mw2])
        spec = AgentSpec(name="test")
        context = AgentContext(request_id="r1", query="q")

        async def inner(s, c, q):
            order.append("inner")
            return "ok"

        result = await chain.run(spec, context, "q", inner=inner)
        assert result == "ok"
        assert order == ["mw1_before", "mw2_before", "inner", "mw2_after", "mw1_after"]

    async def test_add_method_appends_layer(self):
        order: list[str] = []

        async def mw1(ctx, next_fn):
            order.append("mw1")
            return await next_fn(ctx)

        async def mw2(ctx, next_fn):
            order.append("mw2")
            return await next_fn(ctx)

        chain = MiddlewareChain()
        chain.add(mw1)
        chain.add(mw2)

        spec = AgentSpec(name="test")
        context = AgentContext(request_id="r1", query="q")

        async def inner(s, c, q):
            order.append("inner")
            return "ok"

        result = await chain.run(spec, context, "q", inner=inner)
        assert result == "ok"
        assert order == ["mw1", "mw2", "inner"]