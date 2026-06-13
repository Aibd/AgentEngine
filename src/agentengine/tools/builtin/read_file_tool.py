"""ReadFileTool — read-only file access tool.

Reads UTF-8 text files from one or more workspace roots. Intentionally
constrained: read-only, size-capped, workspace-bounded. For production use
alongside a sandbox, the sandbox host workspace is automatically included
as an additional root so the model can read files produced by sandboxed
bash/python execution.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from agentengine.tools.base import Tool

logger = logging.getLogger(__name__)


READ_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Path to the file to read. Relative paths are resolved "
                "against the agent workspace roots. Absolute paths are "
                "accepted when the file exists under a known root."
            ),
        },
        "max_bytes": {
            "type": "integer",
            "description": "Optional cap on bytes returned (default 8192).",
            "minimum": 1,
            "maximum": 65536,
        },
    },
    "required": ["path"],
}


def _resolve_root(root: str | os.PathLike[str] | None) -> Path | None:
    if root is None:
        return None
    try:
        return Path(root).resolve()
    except (OSError, RuntimeError):
        return None


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a UTF-8 text file from the workspace and return its contents. "
        "Use this when you need to inspect source code, configuration, or notes."
    )
    schema = READ_FILE_SCHEMA
    timeout_seconds = 5.0
    max_result_chars = 8000
    result_summary_strategy = "head_tail"

    def __init__(
        self,
        workspace_root: str | os.PathLike[str] | None = None,
        *,
        extra_roots: list[str | os.PathLike[str]] | None = None,
    ) -> None:
        # Primary root: explicit arg → AGENT_WORKSPACE_ROOT env → cwd
        primary = workspace_root
        if primary is None:
            primary = Path(os.getenv("AGENT_WORKSPACE_ROOT", os.getcwd()))
        self._roots: list[Path] = []
        root = _resolve_root(primary)
        if root is not None:
            self._roots.append(root)
        for extra in (extra_roots or []):
            extra_root = _resolve_root(extra)
            if extra_root is not None and extra_root not in self._roots:
                self._roots.append(extra_root)

    async def run(self, **kwargs: Any) -> str:
        raw_path = str(kwargs.get("path", "")).strip()
        if not raw_path:
            return "Error: 'path' is required."

        max_bytes = int(kwargs.get("max_bytes") or 8192)
        max_bytes = max(1, min(max_bytes, 65536))

        resolved, reason = self._resolve_path(raw_path)
        if resolved is None:
            roots_desc = "\n  ".join(str(r) for r in self._roots) or "(none)"
            if reason == "traversal":
                return (
                    f"Error: path escapes workspace root(s): '{raw_path}'\n"
                    f"Workspace roots:\n  {roots_desc}"
                )
            # reason == "missing"
            return (
                f"Error: file not found: '{raw_path}'\n"
                f"Searched roots:\n  {roots_desc}"
            )
        if not resolved.is_file():
            return f"Error: not a regular file: {resolved}"

        try:
            data = resolved.read_bytes()[:max_bytes]
            text = data.decode("utf-8", errors="replace")
        except OSError as exc:
            return f"Error: read failed: {exc}"

        # Find which root this file belongs to for the header.
        display_root = self._find_root_for(resolved)
        relative = str(resolved.relative_to(display_root)) if display_root else str(resolved)
        size = resolved.stat().st_size
        truncated = size > max_bytes
        header = f"# {relative} ({size} bytes"
        if truncated:
            header += f", showing first {max_bytes})"
        else:
            header += ")"
        logger.info("read_file_tool_invoked path=%s bytes=%d", resolved, len(data))
        return f"{header}\n{text}"

    # -- internal helpers -----------------------------------------------------

    def _resolve_path(self, raw: str) -> tuple[Path | None, str]:
        """Return ``(resolved_path, reason)``. *reason* is ``"ok"``, ``"missing"``,
        or ``"traversal"`` to distinguish "file not found" from "path escapes root".
        """
        path = Path(raw)

        # Absolute path: resolve and check if it's under any root.
        if path.is_absolute():
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError):
                return None, "missing"
            if not resolved.exists():
                return None, "missing"
            if self._is_under_any_root(resolved):
                return resolved, "ok"
            # If no roots are configured, accept any existing absolute path
            # (the OS enforces read permissions; this is a read-only tool).
            if not self._roots:
                return resolved, "ok"
            return None, "traversal"

        # Relative path: try each root. Check traversal FIRST (before
        # existence) so `../etc/passwd` is caught even if the file doesn't
        # actually exist on this machine.
        for root in self._roots:
            try:
                candidate = (root / raw).resolve()
            except (OSError, RuntimeError):
                continue
            # Traversal check first — independent of file existence.
            if not self._is_under_root(candidate, root):
                return None, "traversal"
            if candidate.exists():
                return candidate, "ok"

        # No root matched (either file truly missing, or no roots configured).
        return None, "missing"

    def _is_under_any_root(self, path: Path) -> bool:
        if not self._roots:
            return True  # no roots → no restriction
        for root in self._roots:
            if self._is_under_root(path, root):
                return True
        return False

    @staticmethod
    def _is_under_root(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _find_root_for(self, path: Path) -> Path | None:
        """Return the workspace root that contains *path*, for display."""
        for root in self._roots:
            if self._is_under_root(path, root):
                return root
        return None
