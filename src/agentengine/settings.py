"""Central deployment configuration — single source of truth for all env-driven settings.

Load order: dotenv file (if provided) → os.environ → defaults coded here.

Usage::

    from agentengine.settings import Settings

    settings = Settings.from_env(dotenv=Path(".env"))
    llm = create_llm_from_env()                      # OpenAICompatibleClient | None
    sandbox_cfg = settings.to_sandbox_config()       # SandboxConfig
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentengine.llm.openai_compat import OpenAICompatibleClient
    from agentengine.sandbox.config import SandboxConfig


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class LLMConfigError(ValueError):
    """Raised when required LLM environment variables are missing or invalid."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_dotenv(path: Path) -> None:
    """Load key=value pairs from *path* into os.environ; existing vars win."""
    if not path.exists():
        return
    for raw_line in path.read_text("utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.split("#")[0].strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _str_env(name: str, default: str = "") -> str:
    val = os.getenv(name)
    return val if val else default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise LLMConfigError(f"{name} must be a float") from exc


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise LLMConfigError(f"{name} must be an integer") from exc


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Sub-settings dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMSettings:
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = ""
    timeout: float = 120.0
    max_retries: int = 2


@dataclass(frozen=True)
class SandboxSettings:
    mem_limit: str = "256m"
    nano_cpus: int = 500_000_000
    pids_limit: int = 64
    max_containers: int = 12
    max_concurrent_execs: int = 0
    idle_ttl_seconds: float = 300.0
    default_exec_timeout: float = 30.0
    sessions_root: str = ""
    network_disabled: bool = True
    tmpfs_tmp_size: str = "32m"


@dataclass(frozen=True)
class ObservabilitySettings:
    log_dir: str = "logs"


@dataclass(frozen=True)
class QuotaSettings:
    max_runs: int = 0
    max_tool_calls: int = 0
    max_tokens_in: int = 0
    max_tokens_out: int = 0
    window_seconds: float = 60.0

    @property
    def has_limits(self) -> bool:
        return any([self.max_runs, self.max_tool_calls, self.max_tokens_in, self.max_tokens_out])


@dataclass(frozen=True)
class AppSettings:
    approval_timeout_seconds: float = 300.0


@dataclass(frozen=True)
class AuthSettings:
    """OIDC resource-server settings for the HTTP API.

    Leaving ``issuer_url`` empty is intentionally not an anonymous fallback:
    the web API returns 503 for protected routes until an operator configures
    an identity provider.
    """

    issuer_url: str = ""
    audience: str = ""
    jwks_url: str = ""
    tenant_claim: str = "tenant_id"
    allowed_algorithms: tuple[str, ...] = ("RS256",)
    jwks_cache_seconds: float = 300.0
    http_timeout_seconds: float = 5.0


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    """All deployment-level configuration, loaded once at startup."""

    llm: LLMSettings = field(default_factory=LLMSettings)
    sandbox: SandboxSettings = field(default_factory=SandboxSettings)
    observability: ObservabilitySettings = field(default_factory=ObservabilitySettings)
    quota: QuotaSettings = field(default_factory=QuotaSettings)
    app: AppSettings = field(default_factory=AppSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)

    @classmethod
    def from_env(cls, dotenv: Path | None = None) -> "Settings":
        """Build Settings from environment variables, optionally loading *dotenv* first."""
        if dotenv is not None:
            _load_dotenv(dotenv)
        return cls(
            llm=LLMSettings(
                base_url=_str_env("LLM_BASE_URL", "https://api.deepseek.com"),
                api_key=_str_env("LLM_API_KEY"),
                model=_str_env("LLM_MODEL"),
                timeout=_float_env("LLM_TIMEOUT", 120.0),
                max_retries=_int_env("LLM_MAX_RETRIES", 2),
            ),
            sandbox=SandboxSettings(
                mem_limit=_str_env("SANDBOX_MEM_LIMIT", "256m"),
                nano_cpus=_int_env("SANDBOX_NANO_CPUS", 500_000_000),
                pids_limit=_int_env("SANDBOX_PIDS_LIMIT", 64),
                max_containers=_int_env("SANDBOX_MAX_CONTAINERS", 12),
                max_concurrent_execs=_int_env("SANDBOX_MAX_CONCURRENT_EXECS", 0),
                idle_ttl_seconds=_float_env("SANDBOX_IDLE_TTL_SECONDS", 300.0),
                default_exec_timeout=_float_env("SANDBOX_EXEC_TIMEOUT", 30.0),
                sessions_root=_str_env("SANDBOX_SESSIONS_ROOT"),
                network_disabled=_bool_env("SANDBOX_NETWORK_DISABLED", True),
                tmpfs_tmp_size=_str_env("SANDBOX_TMPFS_TMP_SIZE", "32m"),
            ),
            observability=ObservabilitySettings(
                log_dir=_str_env("AGENTENGINE_LOG_DIR", "logs"),
            ),
            quota=QuotaSettings(
                max_runs=_int_env("QUOTA_MAX_RUNS", 0),
                max_tool_calls=_int_env("QUOTA_MAX_TOOL_CALLS", 0),
                max_tokens_in=_int_env("QUOTA_MAX_TOKENS_IN", 0),
                max_tokens_out=_int_env("QUOTA_MAX_TOKENS_OUT", 0),
                window_seconds=_float_env("QUOTA_WINDOW_SECONDS", 60.0),
            ),
            app=AppSettings(
                approval_timeout_seconds=_float_env("APPROVAL_TIMEOUT_SECONDS", 300.0),
            ),
            auth=AuthSettings(
                issuer_url=_str_env("OIDC_ISSUER_URL").rstrip("/"),
                audience=_str_env("OIDC_AUDIENCE"),
                jwks_url=_str_env("OIDC_JWKS_URL"),
                tenant_claim=_str_env("OIDC_TENANT_CLAIM", "tenant_id"),
                allowed_algorithms=tuple(
                    item.strip()
                    for item in _str_env("OIDC_ALLOWED_ALGORITHMS", "RS256").split(",")
                    if item.strip()
                ) or ("RS256",),
                jwks_cache_seconds=_float_env("OIDC_JWKS_CACHE_SECONDS", 300.0),
                http_timeout_seconds=_float_env("OIDC_HTTP_TIMEOUT_SECONDS", 5.0),
            ),
        )

    def to_sandbox_config(self) -> "SandboxConfig":
        """Build a SandboxConfig from the current sandbox settings."""
        from agentengine.sandbox.config import SandboxConfig  # avoid circular import

        return SandboxConfig(
            mem_limit=self.sandbox.mem_limit,
            nano_cpus=self.sandbox.nano_cpus,
            pids_limit=self.sandbox.pids_limit,
            max_containers=self.sandbox.max_containers,
            max_concurrent_execs=self.sandbox.max_concurrent_execs,
            idle_ttl_seconds=self.sandbox.idle_ttl_seconds,
            default_exec_timeout=self.sandbox.default_exec_timeout,
            network_disabled=self.sandbox.network_disabled,
            tmpfs={"/tmp": f"size={self.sandbox.tmpfs_tmp_size}"},
        )


# ---------------------------------------------------------------------------
# Standalone factory — kept as public API (documented in README / PUBLIC_API.md)
# ---------------------------------------------------------------------------

def create_llm_from_env(
    *,
    prefix: str = "LLM_",
    required: bool = True,
) -> "OpenAICompatibleClient | None":
    """Create an OpenAI-compatible LLM client from environment variables.

    Required variables (with default prefix ``LLM_``):
      - ``LLM_API_KEY``
      - ``LLM_MODEL``

    Optional variables:
      - ``LLM_BASE_URL``, ``LLM_TIMEOUT``, ``LLM_MAX_RETRIES``
    """
    from agentengine.llm.openai_compat import OpenAICompatibleClient  # avoid circular import

    api_key = os.getenv(f"{prefix}API_KEY")
    model = os.getenv(f"{prefix}MODEL")

    missing = [
        name
        for name, value in (
            (f"{prefix}API_KEY", api_key),
            (f"{prefix}MODEL", model),
        )
        if not value
    ]
    if missing:
        if not required:
            return None
        raise LLMConfigError(f"Missing LLM environment variables: {', '.join(missing)}")

    return OpenAICompatibleClient(
        base_url=os.getenv(f"{prefix}BASE_URL", "https://api.deepseek.com"),
        api_key=api_key or "",
        model=model or "",
        timeout=_float_env(f"{prefix}TIMEOUT", 120.0),
        max_retries=_int_env(f"{prefix}MAX_RETRIES", 2),
    )


__all__ = [
    "LLMConfigError",
    "LLMSettings",
    "SandboxSettings",
    "ObservabilitySettings",
    "QuotaSettings",
    "AppSettings",
    "AuthSettings",
    "Settings",
    "create_llm_from_env",
]
