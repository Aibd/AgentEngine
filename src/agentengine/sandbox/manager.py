"""SandboxManager — per-conversation container lifecycle.

This is the runtime-orchestration peer of ``ConversationLockManager``: it is
keyed by ``conversation_id`` (the same isolation granularity as the lock), so
one conversation maps to exactly one container, one workspace directory, and
one lock. Multiple sessions from the *same user* therefore get fully separate
containers — isolation is per-conversation, not per-user.

Responsibilities:
* acquire(conversation_id) -> a started sandbox (created on first use, reused
  on subsequent execs within the session)
* release(conversation_id)  -> destroy the container and wipe its workspace,
  leaving no residue (prevents cross-session pollution)
* enforce a max-concurrency cap (the 32G host has a hard memory ceiling)
* reap idle sessions past a TTL so zombie conversations stop pinning memory

The manager itself runs on the trusted host. It never executes untrusted code;
it only creates/destroys the containers that do.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from agentengine.sandbox.config import SandboxConfig
from agentengine.sandbox.errors import SandboxCapacityError, SandboxError
from agentengine.sandbox.session_sandbox import SessionSandbox

logger = logging.getLogger(__name__)

# Single execution engine: the resident session container (the doc's "会话容器").
Sandbox = SessionSandbox


class SandboxManager:
    """Create, reuse, reap, and destroy per-conversation sandboxes.

    Thread-safe (guarded by a single lock); intended as a process-wide
    singleton constructed next to the orchestration service, exactly like
    ``InMemoryConversationLockManager``.
    """

    def __init__(
        self,
        *,
        sessions_root: str | Path,
        config: SandboxConfig | None = None,
        docker_client: Any | None = None,
    ) -> None:
        self.sessions_root = Path(sessions_root)
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self.cfg = config or SandboxConfig()
        self._client = docker_client or self._make_client()
        self._sandboxes: dict[str, Sandbox] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _make_client() -> Any:
        try:
            import docker  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise SandboxError(
                "The 'docker' SDK is required for the sandbox. "
                "Install with: pip install agentengine[sandbox]"
            ) from exc
        return docker.from_env()

    # -- acquire / release ----------------------------------------------------

    def acquire(self, conversation_id: str) -> Sandbox:
        """Return a started sandbox for the conversation, creating it on first
        use. Reaps idle sandboxes first so the capacity check is accurate."""
        if not conversation_id:
            raise SandboxError("conversation_id is required to acquire a sandbox")
        self.reap_idle()
        with self._lock:
            existing = self._sandboxes.get(conversation_id)
            if existing is not None:
                return existing
            if len(self._sandboxes) >= self.cfg.max_containers:
                raise SandboxCapacityError(
                    f"sandbox capacity reached ({self.cfg.max_containers} live "
                    "containers); retry later or raise SandboxConfig.max_containers"
                )
            sandbox = self._create(conversation_id)
            self._sandboxes[conversation_id] = sandbox
        sandbox.start()  # network/disk I/O outside the lock
        return sandbox

    def release(self, conversation_id: str) -> None:
        """Destroy the conversation's container and wipe its workspace dir.

        This is the anti-pollution guarantee: nothing survives to leak into a
        later session. Safe to call for an unknown id (no-op)."""
        with self._lock:
            sandbox = self._sandboxes.pop(conversation_id, None)
        if sandbox is None:
            return
        sandbox.close()
        self._wipe_workspace(conversation_id)
        logger.info("sandbox_released conv=%s", conversation_id)

    def reap_idle(self) -> int:
        """Release sandboxes idle longer than ``idle_ttl_seconds``. Returns the
        count reaped. No-op when TTL is 0."""
        ttl = self.cfg.idle_ttl_seconds
        if ttl <= 0:
            return 0
        now = time.monotonic()
        with self._lock:
            stale = [
                cid for cid, sb in self._sandboxes.items()
                if now - sb.last_used > ttl
            ]
        for cid in stale:
            logger.info("sandbox_reaping_idle conv=%s", cid)
            self.release(cid)
        return len(stale)

    def shutdown(self) -> None:
        """Destroy every live sandbox. Call from the app's shutdown hook."""
        with self._lock:
            ids = list(self._sandboxes)
        for cid in ids:
            self.release(cid)

    # -- introspection --------------------------------------------------------

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._sandboxes)

    def host_workspace_for(self, conversation_id: str) -> Path:
        """The host directory bind-mounted into the conversation's container.
        File tools should use this as their workspace root so bash/kernel and
        the file tools see the same filesystem."""
        safe = conversation_id.replace("/", "_").replace(":", "_")
        return self.sessions_root / safe

    # -- internals ------------------------------------------------------------

    def _create(self, conversation_id: str) -> Sandbox:
        return SessionSandbox(
            conversation_id=conversation_id,
            host_workspace=self.host_workspace_for(conversation_id),
            config=self.cfg,
            docker_client=self._client,
        )

    def _wipe_workspace(self, conversation_id: str) -> None:
        host_ws = self.host_workspace_for(conversation_id)
        try:
            if host_ws.exists():
                shutil.rmtree(host_ws, ignore_errors=True)
        except OSError as exc:  # pragma: no cover
            logger.warning("sandbox_wipe_failed path=%s err=%s", host_ws, exc)
