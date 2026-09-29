/**
 * Auth helpers for the web client.
 *
 * OIDC/Keycloak sign-in is currently disabled for local development.
 * `apiFetch` is a thin wrapper around `fetch` so call sites stay stable
 * when authentication is re-enabled later.
 */

export async function initializeAuth(): Promise<void> {
  // No-op while auth is disabled.
}

export async function beginLogin(): Promise<void> {
  // No-op while auth is disabled.
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  return fetch(input, init);
}
