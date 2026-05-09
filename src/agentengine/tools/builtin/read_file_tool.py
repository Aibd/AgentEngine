"""ReadFileTool — minimal, safe demo tool.

Used by examples and the streaming-protocol demos to give the LLM something
real to call. Intentionally constrained: read-only, size-capped, sandboxed
under a configurable workspace root. Not a general-purpose filesystem tool.
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
                "against the agent workspace root."
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

    def __init__(self, workspace_root: str | os.PathLike[str] | None = None) -> None:
        if workspace_root is None:
            root = Path(os.getenv("AGENT_WORKSPACE_ROOT", os.getcwd()))
        else:
            root = Path(workspace_root)
        self._root = root.resolve()

    async def run(self, **kwargs: Any) -> str:
        raw_path = str(kwargs.get("path", "")).strip()
        if not raw_path:
            return "Error: 'path' is required."

        max_bytes = int(kwargs.get("max_bytes") or 8192)
        max_bytes = max(1, min(max_bytes, 65536))

        candidate = (self._root / raw_path) if not Path(raw_path).is_absolute() else Path(raw_path)
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            return f"Error: cannot resolve path: {exc}"

        # Sandbox: must stay under workspace root.
        try:
            resolved.relative_to(self._root)
        except ValueError:
            return f"Error: path escapes workspace root ({self._root})."

        if not resolved.exists():
            return f"Error: file not found: {resolved}"
        if not resolved.is_file():
            return f"Error: not a regular file: {resolved}"

        try:
            data = resolved.read_bytes()[:max_bytes]
            text = data.decode("utf-8", errors="replace")
        except OSError as exc:
            return f"Error: read failed: {exc}"

        size = resolved.stat().st_size
        truncated = size > max_bytes
        header = f"# {resolved.relative_to(self._root)} ({size} bytes"
        if truncated:
            header += f", showing first {max_bytes})"
        else:
            header += ")"
        logger.info("read_file_tool_invoked path=%s bytes=%d", resolved, len(data))
        return f"{header}\n{text}"
