"""Docker-backed sandbox for running untrusted (LLM-generated) code.

This package is **optional** and **deploy-time pluggable**. The agentengine
core never imports it; a deployment that needs to run untrusted code wires the
sandbox tools into a preset's ``setup`` hook and constructs a
:class:`SandboxManager` alongside the orchestration service.

Trust model (see docs/sandbox-deployment.md):
- The Agent App (TurnRunner / ToolExecutor) is trusted and stays on the host.
- Untrusted code runs *inside* a per-conversation container with the hardening
  profile from :class:`SandboxConfig` (network off, cap drop ALL, non-root,
  cgroup limits, pids limit, no-new-privileges).

Execution engine — the **session container** model (the doc's "会话容器"):
:class:`SessionSandbox` keeps a container resident for the whole conversation
and ``exec``s into it. Files and pip-installed packages persist across calls;
in-memory Python variables do not (each ``python -c`` is a fresh process),
which keeps every execution clean of the previous one's state. This is the
right fit for a skill-style "run a task, emit results + output files" product.

Importing this package requires the ``docker`` SDK::

    pip install agentengine[sandbox]   # or: pip install docker
"""

from __future__ import annotations

from agentengine.sandbox.config import SandboxConfig
from agentengine.sandbox.errors import (
    SandboxCapacityError,
    SandboxError,
    SandboxStartupError,
)
from agentengine.sandbox.manager import SandboxManager
from agentengine.sandbox.session_sandbox import SessionSandbox
from agentengine.sandbox.tools import SandboxedBashTool, SandboxedPythonTool

__all__ = [
    "SandboxConfig",
    "SandboxManager",
    "SessionSandbox",
    "SandboxedBashTool",
    "SandboxedPythonTool",
    "SandboxError",
    "SandboxStartupError",
    "SandboxCapacityError",
]
