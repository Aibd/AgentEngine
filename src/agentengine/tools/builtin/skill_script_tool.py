"""Tool for executing skill scripts inside a sandbox or on the host.

Scripts in skills with ``host_exec: true`` frontmatter run as host subprocesses
(needed for network access, since the sandbox has network_disabled=True).
All other scripts run inside the per-conversation sandbox container.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import sys
from pathlib import Path
from typing import Any

from agentengine.sandbox.manager import SandboxManager
from agentengine.skills.loader import SkillLoader
from agentengine.skills.materializer import SkillMaterializer
from agentengine.skills.paths import SkillPathError, validate_skill_resource_path
from agentengine.tools.base import Tool

logger = logging.getLogger(__name__)


class RunSkillScript(Tool):
    """Execute a script bundled with an enabled skill inside the sandbox.

    The script must be listed in the skill's ``resources.scripts`` manifest. The
    skill package is first copied into the conversation workspace, then the
    script is run with the requested arguments via ``SessionSandbox.exec_argv``.
    """

    name = "RunSkillScript"
    description = (
        "Run a script bundled with a skill inside the isolated sandbox. "
        "Only scripts declared in the skill manifest can be executed."
    )
    schema = {
        "type": "object",
        "properties": {
            "skill": {"type": "string", "description": "Skill name"},
            "script": {
                "type": "string",
                "description": "Script path relative to the skill directory, e.g. scripts/analyze.py",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Arguments passed to the script as a list (no shell parsing).",
            },
            "timeout": {
                "type": "number",
                "description": "Optional timeout in seconds (default 60).",
                "minimum": 1,
                "maximum": 600,
            },
        },
        "required": ["skill", "script"],
    }
    timeout_seconds = 600.0
    max_result_chars = 16000
    result_summary_strategy = "head_tail"

    def __init__(
        self,
        *,
        loader: SkillLoader,
        sandbox_manager: SandboxManager,
        conversation_id: str,
        enabled_names: set[str] | None = None,
        workspace_root: str | Path,
    ) -> None:
        self._loader = loader
        self._sandbox_manager = sandbox_manager
        self._conversation_id = conversation_id
        self._enabled_names = enabled_names
        self._materializer = SkillMaterializer(workspace_root)

    async def run(self, **kwargs: Any) -> str:
        skill_name = str(kwargs.get("skill", ""))
        script_path = str(kwargs.get("script", ""))
        args = list(kwargs.get("args") or [])
        timeout = kwargs.get("timeout")

        skills = self._loader.discover()
        if skill_name not in skills:
            return f"Unknown skill: {skill_name}"

        if self._enabled_names is not None and skill_name not in self._enabled_names:
            return f"Skill is disabled: {skill_name}"

        skill = skills[skill_name]
        if script_path not in skill.resources.scripts:
            return (
                f"'{script_path}' is not a declared script of skill '{skill_name}'. "
                f"Declared scripts: {', '.join(skill.resources.scripts) or '(none)'}"
            )

        try:
            validate_skill_resource_path(skill, script_path)
        except SkillPathError as exc:
            return f"Invalid script path: {exc}"

        interpreter = self._infer_interpreter(script_path)
        argv = [interpreter, script_path, *args]
        timeout_f = float(timeout) if timeout else 60.0

        logger.info(
            "skill_script_run skill=%s script=%s host_exec=%s conv=%s",
            skill_name, script_path, skill.host_exec, self._conversation_id,
        )

        if skill.host_exec:
            result = await self._run_on_host(skill, script_path, argv, timeout_f)
        else:
            materialized = self._materializer.materialize(skill)
            sandbox = self._sandbox_manager.acquire(self._conversation_id)
            result = sandbox.exec_argv(
                argv,
                timeout=timeout_f,
                workdir=materialized.workspace_path,
            )

        return self._format_result(script_path, argv, result)

    @staticmethod
    async def _run_on_host(
        skill: Any,
        script_path: str,
        argv: list[str],
        timeout: float,
    ) -> dict[str, Any]:
        """Run a skill script as a host subprocess (for network-dependent skills)."""
        script_full = str(skill.base_dir / script_path)
        host_argv = [argv[0], script_full, *argv[2:]]  # replace relative path with absolute
        try:
            proc = await asyncio.create_subprocess_exec(
                *host_argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(skill.base_dir),
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                timed_out = False
                exit_code = proc.returncode or 0
            except asyncio.TimeoutError:
                proc.kill()
                stdout_b, stderr_b = await proc.communicate()
                timed_out = True
                exit_code = -1
        except Exception as exc:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"host exec error: {exc}",
                "timed_out": False,
            }
        return {
            "exit_code": exit_code,
            "stdout": stdout_b.decode("utf-8", errors="replace"),
            "stderr": stderr_b.decode("utf-8", errors="replace"),
            "timed_out": timed_out,
        }

    @staticmethod
    def _infer_interpreter(script_path: str) -> str:
        if script_path.endswith(".py"):
            return "python"
        if script_path.endswith(".sh"):
            return "bash"
        return "sh"

    @staticmethod
    def _format_result(script_path: str, argv: list[str], result: dict[str, Any]) -> str:
        parts = [f"$ {shlex.join(argv)}", f"exit={result['exit_code']}"]
        if result.get("timed_out"):
            parts.append("[!] command timed out")
        stdout = result.get("stdout", "").rstrip("\n")
        stderr = result.get("stderr", "").rstrip("\n")
        if stdout:
            parts += ["--- stdout ---", stdout]
        if stderr:
            parts += ["--- stderr ---", stderr]
        if not stdout and not stderr and result.get("exit_code") == 0:
            parts.append("(no output)")
        return "\n".join(parts)
