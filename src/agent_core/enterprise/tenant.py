"""Multi-tenant identity context.

``TenantContext`` carries tenant identity and scope information and is injected
into ``AgentContext.extras`` so that every ``RuntimeEvent`` and downstream
middleware can access it without threading parameters through every function
call.

Design:
- A single process can host many tenants.
- ``tenant_id`` is the isolation boundary — one tenant must never see another's
  messages, runs, or state.
- ``scopes`` are coarse-grained feature flags ("tools:*", "models:*").
- ``user_id`` and ``session_id`` support per-user quotas and audit trails.

Integration:
    from agent_core.enterprise.tenant import TenantContext

    ctx = AgentContext(request_id="...", query="...")
    ctx.extras["tenant"] = TenantContext(
        tenant_id="acme",
        user_id="user-42",
        scopes=["tools:read_file", "models:gpt-4o-mini"],
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class TenantContext:
    """Identity scope for one tenant/user/session."""

    tenant_id: str
    user_id: str = ""
    session_id: str = ""
    scopes: list[str] = field(default_factory=list)

    def has_scope(self, scope: str) -> bool:
        """Check if this tenant is allowed a particular scope.

        Supports wildcard matching: ``"tools:*"`` grants all ``"tools:..."`` scopes.
        """
        if not self.scopes:
            return False
        for s in self.scopes:
            if s == scope:
                return True
            if s.endswith(":*") and scope.startswith(s[:-1]):
                return True
        return False

    @property
    def identity(self) -> str:
        """Stable composite identifier for logs and traces."""
        parts = [self.tenant_id]
        if self.user_id:
            parts.append(self.user_id)
        return ":".join(parts)