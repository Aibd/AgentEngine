"""SandboxManager — per-conversation container lifecycle.

This is the runtime-orchestration peer of ``ConversationLockManager``: it is
keyed by ``conversation_id`` (the same isolation granularity as the lock), so
one conversation maps to exactly one container, one workspace directory, and
one lock. Multiple sessions from the *same user* therefore get fully separate
containers — isolation is per-conversation, not per-user.

The resident container model ("会话容器"):
* ``acquire()`` creates a ``sleep infinity`` container on first use and caches
  it so subsequent execs within the same conversation reuse it.
* ``pip install``'d packages and container-local state persist across execs.
* A background reaper thread destroys containers idle longer than
  ``cfg.idle_ttl_seconds``, preventing memory leaks.

Concurrency is gated by a semaphore (``cfg.max_concurrent_execs``) so a spike
of simultaneous execs doesn't exhaust the host.
"""

from __future__ import annotations

import hashlib
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

    _DEFAULT_REAP_INTERVAL = 60.0  # scan for idle containers every 60s

    def __init__(
        self,
        *,
        sessions_root: str | Path,
        config: SandboxConfig | None = None,
        docker_client: Any | None = None,
        reap_interval: float | None = None,
    ) -> None:
        self.sessions_root = Path(sessions_root)
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self.cfg = config or SandboxConfig()
        self._client = docker_client or self._make_client()
        self._sandboxes: dict[str, Sandbox] = {}
        self._lock = threading.RLock()

        # Concurrency gate: limits simultaneous execs across all containers.
        # Acquired before exec_run, released after. Separate from the container
        # count — a conversation may hold a container but have zero active execs.
        self._exec_sem = (
            threading.Semaphore(self.cfg.max_concurrent_execs)
            if self.cfg.max_concurrent_execs > 0
            else None
        )

        # Background reaper — guarantees idle containers don't leak memory.
        self._reap_interval = (
            reap_interval if reap_interval is not None
            else self._DEFAULT_REAP_INTERVAL
        )
        self._reaper_stop = threading.Event()
        self._reaper_thread: threading.Thread | None = None
        if self._reap_interval > 0 and self.cfg.idle_ttl_seconds > 0:
            self._reaper_thread = threading.Thread(
                target=self._reap_loop,
                name="sandbox-reaper",
                daemon=True,
            )
            self._reaper_thread.start()

    @staticmethod
    def _make_client() -> Any:
        try:
            import docker
        except ImportError as exc:  # pragma: no cover
            raise SandboxError(
                "The 'docker' SDK is required for the sandbox. "
                "Install with: pip install agentengine[sandbox]"
            ) from exc
        return docker.from_env()

    # -- acquire / release ----------------------------------------------------

    def acquire(self, conversation_id: str) -> Sandbox:
        """Return a started sandbox for the conversation, creating it on first
        use. The container lives for the conversation duration and is reaped
        when idle past ``cfg.idle_ttl_seconds`` by a background thread."""
        if not conversation_id:
            raise SandboxError("conversation_id is required to acquire a sandbox")
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

    # -- reaper ----------------------------------------------------------------

    def _reap_loop(self) -> None:
        """Background thread: periodically reap idle containers.

        The lock is held only long enough to identify stale entries and pop
        them from the dict; ``close()`` and ``wipe_workspace()`` (Docker +
        filesystem I/O) happen outside the lock so new ``acquire()`` calls
        are not blocked."""
        while not self._reaper_stop.wait(self._reap_interval):
            stale_entries: list[tuple[str, Sandbox]] = []
            with self._lock:
                ttl = self.cfg.idle_ttl_seconds
                if ttl > 0:
                    now = time.monotonic()
                    for cid, sb in list(self._sandboxes.items()):
                        if now - sb.last_used > ttl:
                            stale_entries.append((cid, sb))
                            self._sandboxes.pop(cid, None)
            # I/O outside the lock
            for cid, sb in stale_entries:
                logger.info("sandbox_reaping_idle conv=%s", cid)
                try:
                    sb.close()
                except Exception:
                    pass
                try:
                    self._wipe_workspace(cid)
                except Exception:
                    pass
            if stale_entries:
                logger.info("sandbox_reaper_cycle reaped=%d", len(stale_entries))

    def shutdown(self) -> None:
        """Destroy every live sandbox. Call from the app's shutdown hook."""
        self._reaper_stop.set()
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
        digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()
        return self.sessions_root / f"session-{digest}"

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
