"""Sandbox-backed tools that implement the agentengine ``Tool`` interface.

The whole point: keep the tool ``name`` identical to the in-process builtins
("bash", and a new "python") so everything above the tool boundary —
``ToolExecutor``, ``ExecPolicy``, the OpenAI tool schema, the SSE events — is
unchanged. We only swap the *body* of ``run()`` from a host subprocess to an
``exec`` inside the per-conversation :class:`SessionSandbox` container.

Wire these into a preset's ``setup`` hook instead of ``BashTool`` when the
deployment must run untrusted code. See docs/sandbox-deployment.md.
"""

from __future__ import annotations

import logging
from typing import Any

from agentengine.sandbox.manager import SandboxManager
from agentengine.tools.base import Tool

logger = logging.getLogger(__name__)


_BASH_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "The shell command to execute."},
        "timeout": {
            "type": "number",
            "description": "Optional timeout in seconds (default 30).",
            "minimum": 1,
            "maximum": 600,
        },
        "description": {
            "type": "string",
            "description": "Short, active-voice description of what this command does.",
        },
    },
    "required": ["command"],
}

_PYTHON_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "description": "Python source to execute."},
        "timeout": {
            "type": "number",
            "description": "Optional timeout in seconds (default 30).",
            "minimum": 1,
            "maximum": 600,
        },
    },
    "required": ["code"],
}


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit // 2
    return f"{text[:head]}\n...[truncated]...\n{text[-(limit - head):]}"


class SandboxedBashTool(Tool):
    """``bash`` tool whose command runs inside the conversation's container.

    Drop-in replacement for the in-process ``BashTool``: same name, same schema.
    The container is resolved per-call from the :class:`SandboxManager` using
    the conversation id, so concurrent conversations hit separate containers.
    """

    name = "bash"
    description = (
        "Execute a shell command inside an isolated sandbox container and return "
        "its combined stdout/stderr plus exit code. The sandbox has no network "
        "by default and a writable /workspace shared with the file tools."
    )
    schema = _BASH_SCHEMA
    timeout_seconds = 600.0  # outer guard; inner timeout is enforced in-container
    max_result_chars = 16000
    result_summary_strategy = "head_tail"
    is_destructive = False

    MAX_STDOUT = 12000
    MAX_STDERR = 4000

    def __init__(self, *, manager: SandboxManager, conversation_id: str) -> None:
        self._mgr = manager
        self._conv = conversation_id

    async def run(self, **kwargs: Any) -> str:
        command = str(kwargs.get("command", "")).strip()
        if not command:
            return "Error: 'command' is required."
        timeout = kwargs.get("timeout")
        sandbox = self._mgr.acquire(self._conv)
        # SessionSandbox.exec_shell is blocking C I/O; for high concurrency wrap
        # in asyncio.to_thread. Kept direct here for clarity.
        result = sandbox.exec_shell(command, timeout=float(timeout) if timeout else None)

        parts: list[str] = [f"$ {command}", f"exit={result['exit_code']}"]
        if result["timed_out"]:
            parts.append("[!] command timed out (exit 124)")
        out = _truncate(result["stdout"].rstrip("\n"), self.MAX_STDOUT)
        err = _truncate(result["stderr"].rstrip("\n"), self.MAX_STDERR)
        if out:
            parts += ["--- stdout ---", out]
        if err:
            parts += ["--- stderr ---", err]
        if not out and not err and result["exit_code"] == 0:
            parts.append("(no output)")
        return "\n".join(parts)


class SandboxedPythonTool(Tool):
    """``python`` tool — runs code inside the session container.

    Each call is a fresh ``python -c`` process: files written under /workspace
    persist across calls, but in-memory variables do not — every execution
    starts clean of the previous one's state. Produce charts/tables by writing
    them to files under /workspace (e.g. ``plt.savefig('/workspace/chart.png')``),
    which the host then collects and ships to object storage.
    """

    name = "python"
    description = (
        "Execute Python code inside an isolated sandbox. Files written under "
        "/workspace persist across calls and are collected as output files; "
        "in-memory variables do not persist between calls. Save charts/tables "
        "as files under /workspace rather than returning objects."
    )
    schema = _PYTHON_SCHEMA
    timeout_seconds = 600.0
    max_result_chars = 16000
    result_summary_strategy = "head_tail"
    is_destructive = False

    def __init__(self, *, manager: SandboxManager, conversation_id: str) -> None:
        self._mgr = manager
        self._conv = conversation_id

    async def run(self, **kwargs: Any) -> str:
        code = str(kwargs.get("code", ""))
        if not code.strip():
            return "Error: 'code' is required."
        timeout = kwargs.get("timeout")
        t = float(timeout) if timeout else None
        sandbox = self._mgr.acquire(self._conv)

        result = sandbox.exec_python(code, timeout=t)
        lines: list[str] = [f"exit={result['exit_code']}"]
        if result["timed_out"]:
            lines.append("[!] timed out (exit 124)")
        if result["stdout"].strip():
            lines += ["--- stdout ---", result["stdout"].rstrip("\n")]
        if result["stderr"].strip():
            lines += ["--- stderr ---", result["stderr"].rstrip("\n")]
        return "\n".join(lines)
