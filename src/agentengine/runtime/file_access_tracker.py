"""Per-run tracker that records which files the agent has read or written.

The tracker subscribes to ``RuntimeEvent``s emitted during a turn. When it
sees a successful ``read_file`` (or any tool we trust to have shown the
agent the current contents) it marks the path as "read". When it sees
``write_file`` / ``edit_file`` succeed it marks the path as "written". The
:class:`FileWriteTool` / :class:`FileEditTool` then ask ``has_read(path)``
before allowing an overwrite.

This is intentionally event-driven rather than tool-side bookkeeping: tools
don't need to know about the tracker, and the tracker can be replaced with
a no-op for tests or short-lived runs without touching tool code.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from agentengine.runtime.events import (
    RuntimeEvent,
    ToolCallCompleted,
    ToolCallStarted,
)

logger = logging.getLogger(__name__)


# Tool names whose successful invocation should mark a path as "read". We
# include glob / grep here because the agent does see content (paths for
# glob, matching lines for grep) — enough to satisfy the read-before-write
# invariant, matching claude-code's behaviour.
_READ_TOOLS: frozenset[str] = frozenset({"read_file", "glob", "grep"})
_WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file"})


class TurnFileAccessTracker:
    """Tracks read/write access to workspace files within a single run.

    Thread-safety: tools may run from different asyncio tasks; we guard the
    sets with an ``RLock`` so concurrent ``observe`` and ``has_read`` calls
    don't race. Cost is negligible — we only touch the lock on tool boundaries.
    """

    def __init__(self, workspace_root: str | os.PathLike[str] | None = None) -> None:
        if workspace_root is None:
            root = Path(os.getenv("AGENT_WORKSPACE_ROOT", os.getcwd()))
        else:
            root = Path(workspace_root)
        self._root = root.resolve()
        self._read: set[Path] = set()
        self._written: set[Path] = set()
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    # -- FileAccessTracker protocol surface ---------------------------------

    def has_read(self, path: Path) -> bool:
        normalized = self._normalize(path)
        if normalized is None:
            return False
        with self._lock:
            return normalized in self._read

    def mark_written(self, path: Path) -> None:
        normalized = self._normalize(path)
        if normalized is None:
            return
        with self._lock:
            self._written.add(normalized)
            # Writing implies the agent now knows the contents — keep
            # subsequent edits permitted without re-reading.
            self._read.add(normalized)

    # -- Runtime-event hook --------------------------------------------------

    def observe(self, event: RuntimeEvent) -> None:
        """Inspect a runtime event and update tracking sets accordingly.

        Cheap and idempotent: unrelated events are ignored, set adds are
        deduplicated. Wire this into TurnRunner's emitter so every event
        flowing through the run touches us first.
        """
        if isinstance(event, ToolCallStarted):
            if event.tool_name in _READ_TOOLS or event.tool_name in _WRITE_TOOLS:
                # Stash a copy of arguments so the matching Completed event
                # can mark the right path even if the executor mutates the
                # arguments dict mid-flight.
                with self._lock:
                    self._pending[event.tool_call_id] = dict(event.arguments)
            return
        if isinstance(event, ToolCallCompleted):
            if event.tool_name in _READ_TOOLS or event.tool_name in _WRITE_TOOLS:
                with self._lock:
                    args = self._pending.pop(event.tool_call_id, None) or {}
                self._absorb_completed(event.tool_name, args)
            return

    # -- Introspection (for tests and diagnostics) ---------------------------

    def snapshot(self) -> dict[str, list[str]]:
        with self._lock:
            return {
                "read": sorted(str(p) for p in self._read),
                "written": sorted(str(p) for p in self._written),
            }

    # -- Internals -----------------------------------------------------------

    def _absorb_completed(self, tool_name: str, arguments: dict[str, Any]) -> None:
        candidates = _extract_paths(arguments)
        if not candidates:
            return
        targets: list[Path] = []
        for raw in candidates:
            normalized = self._normalize(raw)
            if normalized is not None:
                targets.append(normalized)
        if not targets:
            return
        with self._lock:
            if tool_name in _WRITE_TOOLS:
                for path in targets:
                    self._written.add(path)
                    self._read.add(path)
            else:
                for path in targets:
                    self._read.add(path)
        logger.debug(
            "file_access_tracker tool=%s targets=%s",
            tool_name,
            [str(p) for p in targets],
        )

    def _normalize(self, raw: Any) -> Path | None:
        if raw is None:
            return None
        try:
            candidate = Path(str(raw))
        except (TypeError, ValueError):
            return None
        if not candidate.is_absolute():
            candidate = self._root / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            return None
        return resolved


def _extract_paths(arguments: dict[str, Any]) -> list[str]:
    """Pull path-like values out of tool arguments.

    Most tools use ``path`` (read_file, write_file, edit_file). Glob/Grep
    can take a ``path`` to scope the search but their primary input is
    ``pattern`` / ``query`` — we only mark the explicit ``path`` argument
    because the pattern alone doesn't point at a single file.
    """
    if not isinstance(arguments, dict):
        return []
    out: list[str] = []
    raw_path = arguments.get("path")
    if isinstance(raw_path, str) and raw_path:
        out.append(raw_path)
    return out


__all__ = ["TurnFileAccessTracker"]
