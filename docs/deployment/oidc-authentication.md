# OIDC API authentication

The FastAPI service is an OIDC resource server. Every `/api` endpoint other
than `/api/health` requires a verified `Authorization: Bearer <access-token>`.
If `OIDC_ISSUER_URL` is missing, protected endpoints fail closed with `503`.

Configure these values in the deployment environment (not in source control):

```dotenv
OIDC_ISSUER_URL=https://id.example.com/realms/agentengine
OIDC_AUDIENCE=agentengine-api
OIDC_TENANT_CLAIM=tenant_id
OIDC_ALLOWED_ALGORITHMS=RS256
# Optional. When omitted, the server reads jwks_uri from OIDC discovery.
OIDC_JWKS_URL=
```

The token must contain `sub`, the configured tenant claim, `iss`, and (when
configured) `aud`. The API derives tenant, user, and scopes from those verified
claims; request query/body fields cannot override them.

Required scopes are:

- `agent:read` for catalogs and expert discovery; `agent:run` for run/history.
- `reports:read` and `reports:write` for report files and artifacts.
- `skills:read` and `skills:manage` for skill lifecycle and marketplace.
- `connectors:manage` for MCP connector changes.
- `approvals:decide` for destructive-action approvals.

Use an Authorization Code + PKCE flow in the browser, then attach the access
token to API calls. Do not store tenant identity or service credentials in
browser-controlled query parameters.

## Local self-hosted Keycloak

The repository's Docker Compose stack includes a local Keycloak instance and a
preconfigured `agentengine` realm.  It starts automatically with
`docker compose up -d --build`:

- Application: http://localhost:8080
- Keycloak administration: http://localhost:8081/admin
- Initial administrator: `admin` / `change-this-local-admin-password`
- Initial application user: `demo` / `demo-password-change-me`

Change `KEYCLOAK_ADMIN_PASSWORD` in `.env` before using anything other than a
local Docker Desktop setup.  The Keycloak port is deliberately bound to
`127.0.0.1`; the included `start-dev` configuration and embedded database are
not a production deployment.  For production, run Keycloak with `start`, a
managed Postgres database, HTTPS, backups, and an external secret store.
