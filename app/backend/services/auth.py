"""OIDC Bearer-token validation for the web API.

The API deliberately trusts identity, tenant and permissions only after the
JWT signature and standard OIDC claims have been verified.  It never accepts
those values from query parameters or request JSON.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request

from agentengine.settings import AuthSettings


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    tenant_id: str
    scopes: frozenset[str]

    def require_scopes(self, required: set[str]) -> None:
        if required and not required.issubset(self.scopes):
            raise HTTPException(status_code=403, detail="insufficient token scope")


class OIDCAuthenticator:
    """Validate OIDC access tokens against the issuer's published JWKS."""

    def __init__(self, settings: AuthSettings) -> None:
        self._settings = settings
        self._jwks: dict[str, Any] | None = None
        self._jwks_expires_at = 0.0
        self._lock = asyncio.Lock()

    async def authenticate(self, authorization: str | None) -> Principal:
        if not self._settings.issuer_url:
            raise HTTPException(status_code=503, detail="OIDC is not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Bearer token is required")
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            raise HTTPException(status_code=401, detail="Bearer token is required")
        try:
            header = jwt.get_unverified_header(token)
            kid = str(header.get("kid") or "")
            if not kid:
                raise ValueError("JWT header has no kid")
            key = await self._key_for(kid)
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self._settings.allowed_algorithms),
                audience=self._settings.audience or None,
                issuer=self._settings.issuer_url,
                options={"verify_aud": bool(self._settings.audience)},
            )
        except (jwt.PyJWTError, ValueError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=401, detail="invalid access token") from exc

        subject = str(claims.get("sub") or "").strip()
        tenant_id = str(claims.get(self._settings.tenant_claim) or "").strip()
        if not subject or not tenant_id:
            raise HTTPException(status_code=403, detail="token is missing subject or tenant claim")
        return Principal(subject=subject, tenant_id=tenant_id, scopes=_claims_scopes(claims))

    async def _key_for(self, kid: str) -> Any:
        jwks = await self._load_jwks()
        for item in jwks.get("keys", []):
            if item.get("kid") == kid:
                return jwt.PyJWK.from_dict(item).key
        # Key rotation may have happened; refresh once instead of accepting an
        # unknown key or permanently rejecting newly-issued tokens.
        self._jwks_expires_at = 0.0
        jwks = await self._load_jwks()
        for item in jwks.get("keys", []):
            if item.get("kid") == kid:
                return jwt.PyJWK.from_dict(item).key
        raise ValueError("unknown JWT key id")

    async def _load_jwks(self) -> dict[str, Any]:
        if self._jwks is not None and time.monotonic() < self._jwks_expires_at:
            return self._jwks
        async with self._lock:
            if self._jwks is not None and time.monotonic() < self._jwks_expires_at:
                return self._jwks
            jwks_url = self._settings.jwks_url
            async with httpx.AsyncClient(timeout=self._settings.http_timeout_seconds) as client:
                if not jwks_url:
                    discovery_url = self._settings.issuer_url.rstrip("/") + "/.well-known/openid-configuration"
                    discovery = (await client.get(discovery_url)).raise_for_status().json()
                    jwks_url = str(discovery.get("jwks_uri") or "")
                if not jwks_url:
                    raise ValueError("OIDC discovery document has no jwks_uri")
                jwks = (await client.get(jwks_url)).raise_for_status().json()
            if not isinstance(jwks.get("keys"), list):
                raise ValueError("invalid JWKS response")
            self._jwks = jwks
            self._jwks_expires_at = time.monotonic() + self._settings.jwks_cache_seconds
            return jwks


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


def _claims_scopes(claims: dict[str, Any]) -> frozenset[str]:
    values: set[str] = set()
    scope = claims.get("scope")
    if isinstance(scope, str):
        values.update(part for part in scope.split() if part)
    scp = claims.get("scp")
    if isinstance(scp, list):
        values.update(str(part) for part in scp if str(part))
    return frozenset(values)


__all__ = ["OIDCAuthenticator", "Principal", "current_principal"]
