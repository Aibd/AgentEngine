"""Human-in-the-loop approval for destructive tool calls.

``ApprovalGate`` pauses execution and waits for an external approval decision
before a destructive tool runs. The turn loop checks ``context.approval_gate``
first, then the legacy ``context.extras`` key, before executing tools with
``is_destructive=True``.

Integration:
    gate = ApprovalGate(timeout_seconds=300.0)
    context.approval_gate = gate
    # The turn loop then auto-checks destructive tools
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agentengine.enterprise.middleware import MiddlewareContext, MiddlewareFn
from agentengine.errors import AgentEngineError

logger = logging.getLogger(__name__)


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"


@dataclass(slots=True)
class ApprovalResult:
    approved: bool
    reason: str = ""


class ApprovalDeniedError(AgentEngineError):
    code = "approval_denied"
    category = "approval"
    retryable = False

    def __init__(self, tool_name: str, approval_id: str, reason: str = "") -> None:
        super().__init__(
            f"Tool '{tool_name}' denied: {reason}",
            details={
                "tool_name": tool_name,
                "approval_id": approval_id,
                "reason": reason,
            },
        )


class ApprovalGate:
    """Gate for human-in-the-loop tool approval.

    Pending requests are held in memory. Subclass for Redis / pub-sub backends.
    """

    def __init__(self, timeout_seconds: float = 300.0) -> None:
        self._pending: dict[str, asyncio.Future[ApprovalResult]] = {}
        self._timeout = timeout_seconds

    async def request_approval(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        run_id: str = "",
        tenant_id: str = "",
        approval_id: str = "",
    ) -> ApprovalResult:
        approval_id = approval_id or f"apr_{uuid.uuid4().hex[:10]}"
        future: asyncio.Future[ApprovalResult] = asyncio.get_event_loop().create_future()
        self._pending[approval_id] = future
        logger.info("approval_required id=%s tool=%s", approval_id, tool_name)

        try:
            result = await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError:
            self._pending.pop(approval_id, None)
            raise ApprovalDeniedError(tool_name, approval_id, "timed out")

        self._pending.pop(approval_id, None)
        if not result.approved:
            raise ApprovalDeniedError(tool_name, approval_id, result.reason)
        return result

    def resolve(self, approval_id: str, decision: ApprovalResult) -> bool:
        future = self._pending.get(approval_id)
        if future is None or future.done():
            logger.warning("approval_resolve_unknown id=%s", approval_id)
            return False
        future.set_result(decision)
        logger.info("approval_resolved id=%s approved=%s", approval_id, decision.approved)
        return True

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def pending_ids(self) -> list[str]:
        return list(self._pending.keys())


def approval_middleware(gate: ApprovalGate) -> MiddlewareFn:
    """Inject the gate into typed contexts and legacy extras."""

    async def _approval(
        ctx: MiddlewareContext,
        next_fn: Callable[[MiddlewareContext], Awaitable[str]],
    ) -> str:
        ctx.extras["approval_gate"] = gate
        ctx.run_context.extras["approval_gate"] = gate
        ctx.run_context.approval_gate = gate
        return await next_fn(ctx)

    return _approval
