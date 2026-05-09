"""BashTool — execute a shell command in the workspace.

Inspired by claude-code-src `tools/BashTool/`, intentionally trimmed:
no AST parsing, no background tasks, no platform sandbox, no file-history
tracking. We keep what matters for a single-shot agent tool: a working-dir
guard, a timeout, output truncation, and a destructive-command heuristic.

Cross-platform behaviour:
- on POSIX: ``/bin/sh -c <command>``
- on Windows: ``cmd /c <command>``
- callers can override via the ``shell`` constructor argument
  (``"sh"`` | ``"bash"`` | ``"cmd"`` | ``"powershell"`` | ``"pwsh"``)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from agentengine.tools.base import Tool

logger = logging.getLogger(__name__)


BASH_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "The shell command to execute.",
        },
        "timeout": {
            "type": "number",
            "description": (
                "Optional timeout in seconds (default 30, max 600). "
                "Long-running commands should use background runners."
            ),
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


# Command prefixes that should trigger the destructive flag in the result.
# We match on the first non-pipe token; anything matching this list earns a
# warning banner so the executor logs the call at WARNING level.
_DESTRUCTIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*rm\b"),
    re.compile(r"^\s*rmdir\b"),
    re.compile(r"^\s*del\b", re.IGNORECASE),
    re.compile(r"^\s*Remove-Item\b", re.IGNORECASE),
    re.compile(r"^\s*mv\b"),
    re.compile(r"^\s*Move-Item\b", re.IGNORECASE),
    re.compile(r"^\s*git\s+push\s+(--force|-f)\b"),
    re.compile(r"^\s*git\s+reset\s+--hard\b"),
    re.compile(r"^\s*git\s+clean\s+-[fd]"),
    re.compile(r"^\s*git\s+checkout\s+--\s"),
    re.compile(r"^\s*git\s+branch\s+-D\b"),
    re.compile(r"^\s*dd\b"),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"),  # fork bomb
    re.compile(r"^\s*shutdown\b", re.IGNORECASE),
    re.compile(r"^\s*reboot\b", re.IGNORECASE),
    re.compile(r"^\s*halt\b", re.IGNORECASE),
    re.compile(r"^\s*mkfs\b"),
)


def is_destructive_command(command: str) -> bool:
    """Heuristic: does the command look like it could destroy data?

    Used to flag dangerous calls. False positives are fine — they only add a
    log line; we don't block execution. Exposed so other tools can reuse it.
    """
    return any(pattern.search(command) for pattern in _DESTRUCTIVE_PATTERNS)


def _resolve_shell(shell: str | None) -> tuple[str, list[str]]:
    """Resolve the ``shell`` argument into ``(executable, prefix_args)``.

    Returns the path to the shell binary plus the leading args (e.g.
    ``["-c"]`` for sh, ``["/c"]`` for cmd) so the caller can append the
    user-supplied command as the final positional arg.
    """
    selection = (shell or "auto").lower()

    if selection == "auto":
        selection = "cmd" if sys.platform == "win32" else "sh"

    if selection in {"sh", "bash"}:
        binary = shutil.which(selection) or selection
        return binary, ["-c"]
    if selection in {"cmd"}:
        binary = shutil.which("cmd") or "cmd.exe"
        return binary, ["/d", "/s", "/c"]
    if selection in {"powershell", "pwsh"}:
        binary = shutil.which(selection) or shutil.which("pwsh") or shutil.which("powershell") or selection
        return binary, ["-NoProfile", "-NonInteractive", "-Command"]
    raise ValueError(
        f"Unsupported shell '{shell}'. Use one of: auto, sh, bash, cmd, powershell, pwsh."
    )


def _truncate_stream(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = limit // 2
    tail = limit - head
    return f"{text[:head]}\n...[truncated]...\n{text[-tail:]}", True


class BashTool(Tool):
    name = "bash"
    description = (
        "Execute a shell command in the workspace and return its combined "
        "stdout/stderr plus exit code. Use this for git operations, build "
        "tools, package managers, and any other CLI work. Prefer the dedicated "
        "read_file / write_file / edit_file / glob / grep tools for file I/O."
    )
    schema = BASH_TOOL_SCHEMA
    timeout_seconds = 30.0
    max_result_chars = 16000
    result_summary_strategy = "head_tail"
    is_destructive = False  # set per-call dynamically; class default is benign

    # Per-stream truncation (independent of executor-level summarization).
    MAX_STDOUT_CHARS = 12000
    MAX_STDERR_CHARS = 4000
    MAX_TIMEOUT_SECONDS = 600.0

    def __init__(
        self,
        workspace_root: str | os.PathLike[str] | None = None,
        *,
        shell: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        if workspace_root is None:
            root = Path(os.getenv("AGENT_WORKSPACE_ROOT", os.getcwd()))
        else:
            root = Path(workspace_root)
        self._root = root.resolve()
        if not self._root.exists():
            raise FileNotFoundError(f"workspace_root does not exist: {self._root}")
        self._shell_executable, self._shell_prefix = _resolve_shell(shell)
        self._env_overrides = dict(env) if env else None

    async def run(self, **kwargs: Any) -> str:
        command = str(kwargs.get("command", "")).strip()
        if not command:
            return "Error: 'command' is required."

        try:
            timeout = float(kwargs.get("timeout") or self.timeout_seconds)
        except (TypeError, ValueError):
            return "Error: 'timeout' must be a number."
        timeout = max(1.0, min(timeout, self.MAX_TIMEOUT_SECONDS))

        argv = [self._shell_executable, *self._shell_prefix, command]
        env = os.environ.copy()
        if self._env_overrides:
            env.update(self._env_overrides)

        destructive = is_destructive_command(command)
        if destructive:
            logger.warning("bash_tool_destructive_command cmd=%r", command[:200])

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self._root),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return f"Error: shell not found: {exc}"
        except OSError as exc:
            return f"Error: failed to spawn shell: {exc}"

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            return (
                f"Error: command timed out after {timeout:.0f}s.\n"
                f"$ {command}"
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        stdout, stdout_truncated = _truncate_stream(stdout, self.MAX_STDOUT_CHARS)
        stderr, stderr_truncated = _truncate_stream(stderr, self.MAX_STDERR_CHARS)

        exit_code = proc.returncode if proc.returncode is not None else -1

        parts: list[str] = []
        if destructive:
            parts.append("[!] destructive command detected — review the diff before relying on the result.")
        parts.append(f"$ {command}")
        parts.append(f"exit={exit_code}")
        if stdout:
            parts.append("--- stdout ---")
            parts.append(stdout.rstrip("\n"))
            if stdout_truncated:
                parts.append("[stdout truncated]")
        if stderr:
            parts.append("--- stderr ---")
            parts.append(stderr.rstrip("\n"))
            if stderr_truncated:
                parts.append("[stderr truncated]")
        if not stdout and not stderr and exit_code == 0:
            parts.append("(no output)")

        logger.info(
            "bash_tool_invoked exit=%s destructive=%s timeout=%.1fs",
            exit_code, destructive, timeout,
        )
        return "\n".join(parts)
