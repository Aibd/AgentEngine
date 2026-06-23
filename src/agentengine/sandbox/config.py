"""Sandbox configuration — the single source of truth for the hardening profile.

Every knob in the doc's "安全加固清单" (security hardening checklist) lives here
so a deployment can tune limits in one place; :class:`SessionSandbox`
consumes this profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SandboxConfig:
    """Resource limits + isolation profile applied to every sandbox container.

    The defaults are the MVP "Docker + tightened privileges" profile from the
    design doc. They are deliberately conservative; loosen per deployment.
    """

    # --- image ---------------------------------------------------------------
    image: str = "agentengine-sandbox:session"
    """Session sandbox image tag (see deploy/sandbox/Dockerfile.session)."""

    # --- isolation runtime ---------------------------------------------------
    runtime: str | None = None
    """Docker runtime. ``None`` = default (runc). Set to ``"runsc"`` to switch
    the whole fleet to gVisor with zero code changes (see deployment doc)."""

    # --- network -------------------------------------------------------------
    network_disabled: bool = True
    """Hardening checklist #1: no network by default. Untrusted code cannot
    exfiltrate or call out. Flip to False (per deployment) only for a vetted
    'pip install' phase behind a controlled network."""

    # --- user / privileges ---------------------------------------------------
    user: str = "sandbox"
    """Hardening #2: run as a non-root user baked into the image (uid 1000)."""

    cap_drop: tuple[str, ...] = ("ALL",)
    """Hardening #3: drop every Linux capability."""

    no_new_privileges: bool = True
    """Hardening #4: forbid privilege escalation (setuid binaries can't gain)."""

    read_only_rootfs: bool = False
    """Hardening #5: read-only rootfs. Off by default because pip-installed
    packages need writable paths; the workspace bind-mount stays writable
    regardless. Turn on if your image is fully pre-provisioned."""

    # --- cgroup limits -------------------------------------------------------
    mem_limit: str = "256m"
    """Hardening #6: memory ceiling per container (cgroup)."""

    nano_cpus: int = 500_000_000
    """Hardening #6: CPU quota in nano-CPUs. 5e8 = 0.5 cores. 1e9 = 1 core."""

    pids_limit: int = 64
    """Hardening #7: max processes — blocks fork bombs."""

    tmpfs: dict[str, str] = field(default_factory=lambda: {"/tmp": "size=32m"})
    """Writable in-memory scratch. Required when read_only_rootfs=True."""

    # --- workspace mount -----------------------------------------------------
    workspace_mount: str = "/workspace"
    """Path inside the container where the per-session host dir is mounted.
    This is the file-transfer channel (host writes → code reads → results out)
    and also what the file tools should treat as their workspace root."""

    # --- timeouts ------------------------------------------------------------
    default_exec_timeout: float = 30.0
    """Per-exec timeout in seconds (coreutils ``timeout`` wraps each command)."""

    # --- capacity (manager-level) -------------------------------------------
    max_containers: int = 12
    """Max concurrent live containers (resident ``sleep infinity`` containers).
    Worst-case sandbox memory is roughly ``max_containers * mem_limit``; size
    this against the host's memory budget, leaving headroom for the OS, the
    Agent App, and the LLM client."""

    max_concurrent_execs: int = 0
    """Max simultaneous ``exec_run`` calls across all containers. 0 = unlimited.
    A separate knob from ``max_containers`` — a container can be alive but have
    zero active execs. Use this to cap CPU/memory spikes during parallel tool
    calls without limiting the number of resident containers."""

    idle_ttl_seconds: float = 300.0
    """Reap a container after this many seconds with no exec. A background
    reaper thread scans every 60s and destroys idle containers, preventing
    zombie sessions from pinning memory. 0 disables TTL reaping."""

    def host_config_kwargs(self) -> dict[str, Any]:
        """Translate this config into ``docker.containers.run`` kwargs shared by
        both sandbox engines (everything except image / command / volumes)."""
        security_opt: list[str] = []
        if self.no_new_privileges:
            security_opt.append("no-new-privileges")

        kwargs: dict[str, Any] = {
            "network_disabled": self.network_disabled,
            "mem_limit": self.mem_limit,
            "nano_cpus": self.nano_cpus,
            "pids_limit": self.pids_limit,
            "cap_drop": list(self.cap_drop),
            "user": self.user,
            "read_only": self.read_only_rootfs,
            "detach": True,
        }
        if security_opt:
            kwargs["security_opt"] = security_opt
        if self.tmpfs:
            kwargs["tmpfs"] = dict(self.tmpfs)
        if self.runtime:
            kwargs["runtime"] = self.runtime
        return kwargs
