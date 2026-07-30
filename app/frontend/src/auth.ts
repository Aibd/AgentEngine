const issuer = import.meta.env.VITE_OIDC_ISSUER_URL ?? "http://localhost:8081/realms/agentengine";
const clientId = import.meta.env.VITE_OIDC_CLIENT_ID ?? "agentengine-web";
const storagePrefix = "agentengine.oidc.";
const tokenStorageKey = `${storagePrefix}tokens`;
const verifierStorageKey = `${storagePrefix}pkce-verifier`;
const stateStorageKey = `${storagePrefix}state`;

type Tokens = {
  access_token: string;
  expires_at: number;
};

function base64Url(bytes: Uint8Array): string {
  let value = "";
  for (const byte of bytes) value += String.fromCharCode(byte);
  return btoa(value).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function randomValue(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

function redirectUri(): string {
  return `${window.location.origin}${window.location.pathname}`;
}

function readTokens(): Tokens | null {
  const raw = sessionStorage.getItem(tokenStorageKey);
  if (!raw) return null;
  try {
    const tokens = JSON.parse(raw) as Tokens;
    return tokens.access_token && tokens.expires_at > Date.now() + 15_000 ? tokens : null;
  } catch {
    return null;
  }
}

export async function beginLogin(): Promise<never> {
  const verifier = randomValue();
  const state = randomValue();
  sessionStorage.setItem(verifierStorageKey, verifier);
  sessionStorage.setItem(stateStorageKey, state);
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri(),
    response_type: "code",
    scope: "openid profile email",
    state,
    code_challenge: await pkceChallenge(verifier),
    code_challenge_method: "S256",
  });
  window.location.assign(`${issuer}/protocol/openid-connect/auth?${params.toString()}`);
  return new Promise<never>(() => undefined);
}

async function finishLogin(code: string, state: string): Promise<void> {
  const expectedState = sessionStorage.getItem(stateStorageKey);
  const verifier = sessionStorage.getItem(verifierStorageKey);
  sessionStorage.removeItem(stateStorageKey);
  sessionStorage.removeItem(verifierStorageKey);
  if (!expectedState || !verifier || state !== expectedState) {
    throw new Error("Invalid OIDC callback state. Please try signing in again.");
  }
  const response = await fetch(`${issuer}/protocol/openid-connect/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: clientId,
      code,
      redirect_uri: redirectUri(),
      code_verifier: verifier,
    }),
  });
  if (!response.ok) throw new Error(`OIDC token exchange failed: ${response.status}`);
  const payload = (await response.json()) as { access_token?: string; expires_in?: number };
  if (!payload.access_token) throw new Error("OIDC token response did not contain an access token");
  sessionStorage.setItem(tokenStorageKey, JSON.stringify({
    access_token: payload.access_token,
    expires_at: Date.now() + Math.max(1, payload.expires_in ?? 300) * 1000,
  } satisfies Tokens));
  window.history.replaceState({}, document.title, redirectUri());
}

export async function initializeAuth(): Promise<void> {
  const params = new URLSearchParams(window.location.search);
  const error = params.get("error");
  if (error) throw new Error(`OIDC sign-in failed: ${error}`);
  const code = params.get("code");
  const state = params.get("state");
  if (code && state) await finishLogin(code, state);
  if (!readTokens()) await beginLogin();
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const tokens = readTokens();
  if (!tokens) await beginLogin();
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${tokens!.access_token}`);
  const response = await fetch(input, { ...init, headers });
  // A revoked or stale access token should lead back to the login flow rather
  // than leaving the UI on a collection of opaque 401/stream errors.
  if (response.status === 401) {
    sessionStorage.removeItem(tokenStorageKey);
    await beginLogin();
  }
  return response;
}
