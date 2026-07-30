"""Authentication and authorization tests for the OIDC API boundary."""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from fastapi import HTTPException

from agentengine.settings import AuthSettings
from app.backend.services import web_api
from app.backend.services.auth import OIDCAuthenticator, Principal


class _StaticKeyAuthenticator(OIDCAuthenticator):
    async def _key_for(self, kid: str) -> str:
        assert kid == "test-key"
        return "test-secret-key-must-be-at-least-32-bytes"


async def test_oidc_authenticator_uses_verified_token_claims() -> None:
    settings = AuthSettings(
        issuer_url="https://issuer.example",
        audience="agentengine-api",
        tenant_claim="tenant",
        allowed_algorithms=("HS256",),
    )
    token = jwt.encode(
        {
            "sub": "user-7",
            "tenant": "tenant-a",
            "scope": "agent:run reports:read",
            "aud": "agentengine-api",
            "iss": "https://issuer.example",
            "exp": int(time.time()) + 60,
        },
        "test-secret-key-must-be-at-least-32-bytes",
        algorithm="HS256",
        headers={"kid": "test-key"},
    )

    principal = await _StaticKeyAuthenticator(settings).authenticate(f"Bearer {token}")

    assert principal.subject == "user-7"
    assert principal.tenant_id == "tenant-a"
    assert principal.scopes == frozenset({"agent:run", "reports:read"})


async def test_protected_route_rejects_a_missing_or_invalid_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RejectingAuthenticator:
        async def authenticate(self, authorization: str | None) -> Principal:
            raise HTTPException(status_code=401, detail="Bearer token is required")

    monkeypatch.setattr(web_api, "AUTHENTICATOR", _RejectingAuthenticator())
    transport = httpx.ASGITransport(app=web_api.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/capabilities")
    assert response.status_code == 401


async def test_scope_is_enforced_before_a_route_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ReadOnlyAuthenticator:
        async def authenticate(self, authorization: str | None) -> Principal:
            return Principal(subject="u", tenant_id="t", scopes=frozenset({"agent:read"}))

    monkeypatch.setattr(web_api, "AUTHENTICATOR", _ReadOnlyAuthenticator())
    transport = httpx.ASGITransport(app=web_api.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/connectors", json={"name": "forbidden"})
    assert response.status_code == 403


def test_tenant_scopes_are_opaque_and_do_not_trust_client_format() -> None:
    alpha = web_api._scoped_conversation_id("conversation-1", "tenant-alpha")
    bravo = web_api._scoped_conversation_id("conversation-1", "tenant-bravo")
    assert alpha != bravo
    assert "tenant-alpha" not in alpha
