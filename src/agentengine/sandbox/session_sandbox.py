"""SessionSandbox — resident container, ``exec`` runs ``python -c`` / shell.

This is the doc's "会话容器" model — the single execution engine:

* The container stays alive (``sleep infinity``) for the whole conversation.
* Each call ``exec``s into it, so the *expensive* create/start cost is paid
  once per session, not once per execution.
* The workspace is a host directory bind-mounted at ``cfg.workspace_mount`` —
  files written there by the host are visible to the code, and artifacts the
  code writes flow back to the host. Survives across execs.

Each ``python -c`` is a *fresh* process, so in-memory Python variables do NOT
persist between execs (only files and pip-installed packages do). That keeps
every execution clean of the previous one's state — produce charts/tables by
writing them to files under the workspace, which the host then collects.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentengine.sandbox.config import SandboxConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from docker.models.containers import Container

logger = logging.getLogger(__name__)


class SessionSandbox:
    """A per-conversation resident container you ``exec`` into."""

    def __init__(
        self,
        *,
        conversation_id: str,
        host_workspace: str | Path,
        config: SandboxConfig,
        docker_client: Any,
    ) -> None:
        self.conversation_id = conversation_id
        self.host_workspace = Path(host_workspace)
        self.cfg = config
        self._client = docker_client
        self._container: "Container" | None = None
        self.last_used = time.monotonic()

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Create and start the resident container (idempotent)."""
        if self._container is not None:
            return
        self.host_workspace.mkdir(parents=True, exist_ok=True)
        run_kwargs = self.cfg.host_config_kwargs()
        run_kwargs["volumes"] = {
            str(self.host_workspace): {"bind": self.cfg.workspace_mount, "mode": "rw"}
        }
        run_kwargs["working_dir"] = self.cfg.workspace_mount
        self._container = self._client.containers.run(
            image=self.cfg.image,
            command=["sleep", "infinity"],
            name=f"ae-sbx-{self.conversation_id[:32]}",
            labels={"agentengine.sandbox": "session", "conversation_id": self.conversation_id},
            **run_kwargs,
        )
        logger.info(
            "sandbox_session_started conv=%s container=%s",
            self.conversation_id, self._container.short_id,
        )

    def close(self) -> None:
        """Force-remove the container. Idempotent; safe to call twice."""
        if self._container is None:
            return
        try:
            self._container.remove(force=True)
        except Exception as exc:  # noqa: BLE001 - cleanup must never raise
            logger.warning("sandbox_session_remove_failed conv=%s err=%s",
                           self.conversation_id, exc)
        finally:
            self._container = None
            logger.info("sandbox_session_closed conv=%s", self.conversation_id)

    # -- execution ------------------------------------------------------------

    def exec_shell(self, command: str, *, timeout: float | None = None) -> dict[str, Any]:
        """Run a shell command inside the container, wrapped in coreutils
        ``timeout`` so a hung command exits with code 124 instead of blocking."""
        return self._exec(["sh", "-c", command], timeout=timeout, label="shell")

    def exec_python(self, code: str, *, timeout: float | None = None) -> dict[str, Any]:
        """Run a Python snippet via ``python -c``. Fresh process each call —
        in-memory variables do not persist; write outputs to the workspace."""
        return self._exec(["python", "-c", code], timeout=timeout, label="python")

    def exec_argv(
        self,
        argv: list[str],
        *,
        timeout: float | None = None,
        workdir: str | None = None,
    ) -> dict[str, Any]:
        """Run a command specified as an argv array inside the container.

        Prefer this over :meth:`exec_shell` when arguments come from a model or
        other untrusted source to avoid shell injection. The optional *workdir*
        must be a path under the configured workspace mount.
        """
        return self._exec(argv, timeout=timeout, label="argv", workdir=workdir)

    def _exec(
        self,
        argv: list[str],
        *,
        timeout: float | None,
        label: str,
        workdir: str | None = None,
    ) -> dict[str, Any]:
        if self._container is None:
            self.start()
        assert self._container is not None
        self.last_used = time.monotonic()
        t = int(timeout if timeout is not None else self.cfg.default_exec_timeout)
        wrapped = ["timeout", str(t), *argv]
        started = time.perf_counter()

        container_workdir = self._resolve_workdir(workdir)
        result = self._container.exec_run(
            wrapped, demux=True, workdir=container_workdir
        )
        elapsed = time.perf_counter() - started
        out, err = result.output if isinstance(result.output, tuple) else (result.output, None)
        exit_code = result.exit_code if result.exit_code is not None else -1
        payload = {
            "exit_code": exit_code,
            "stdout": (out or b"").decode("utf-8", "replace"),
            "stderr": (err or b"").decode("utf-8", "replace"),
            "timed_out": exit_code == 124,
            "elapsed_seconds": elapsed,
        }
        logger.info(
            "sandbox_session_exec conv=%s kind=%s exit=%s timed_out=%s %.3fs",
            self.conversation_id, label, exit_code, payload["timed_out"], elapsed,
        )
        return payload

    def _resolve_workdir(self, workdir: str | None) -> str:
        """Return a safe container workdir path.

        *workdir* is relative to the configured workspace mount. Empty/None means
        the workspace root. Paths attempting to escape the workspace are rejected.
        """
        if not workdir:
            return self.cfg.workspace_mount

        # Normalise to POSIX-style container path.
        base = self.cfg.workspace_mount.rstrip("/")
        candidate = f"{base}/{workdir.lstrip('/')}"

        # Reject attempts to escape the workspace via '..'.
        resolved = Path(candidate).resolve()
        base_path = Path(base).resolve()
        try:
            resolved.relative_to(base_path)
        except ValueError as exc:
            raise ValueError(f"workdir escapes workspace: {workdir}") from exc

        return candidate
