"""TodoWriteTool — manage a structured task list for the current session.

Modeled after claude-code-src `tools/TodoWriteTool/`. Stores the session
todo list in ``context.extras["todos"]`` (per-run, set up by TurnRunner),
emits a :class:`TodosUpdated` runtime event so SSE consumers can render a
live checklist, and validates the task-state invariants the model is
expected to follow.

Each todo is a dict with three required string fields:

- ``content`` — imperative description ("Run tests")
- ``activeForm`` — present-continuous form ("Running tests")
- ``status`` — one of ``pending`` | ``in_progress`` | ``completed``

Invariants enforced:

- at most one ``in_progress`` task at a time
- ``status`` values are validated against the enum above
- when every task is ``completed``, the stored list is cleared (the model
  is finished — keeping a trailing list pollutes the next turn)
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent_core.runtime.events import RuntimeEvent, TodosUpdated
from agent_core.tools.base import Tool

logger = logging.getLogger(__name__)


_VALID_STATUSES: frozenset[str] = frozenset({"pending", "in_progress", "completed"})


TODO_WRITE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "todos": {
            "type": "array",
            "description": (
                "The full updated todo list. Replaces the current list verbatim — "
                "to update a single task, send the entire list with that task's "
                "new status. Each item needs content, activeForm, and status."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Imperative form: 'Run tests', 'Fix auth bug'.",
                    },
                    "activeForm": {
                        "type": "string",
                        "description": "Present-continuous form: 'Running tests', 'Fixing auth bug'.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed"],
                        "description": "Task state.",
                    },
                },
                "required": ["content", "activeForm", "status"],
            },
        }
    },
    "required": ["todos"],
}


EmitFn = Callable[[RuntimeEvent], Awaitable[None] | None]


class TodoWriteTool(Tool):
    name = "TodoWrite"
    description = (
        "Update the session todo list. Use this proactively for multi-step "
        "tasks: capture the plan up front, mark exactly one item as "
        "in_progress when you start it, mark it completed as soon as it's "
        "done. Skip for single trivial tasks."
    )
    schema = TODO_WRITE_TOOL_SCHEMA
    timeout_seconds = 5.0
    max_result_chars = 2000
    result_summary_strategy = "none"

    def __init__(
        self,
        *,
        store: dict[str, Any] | None = None,
        emit: EmitFn | None = None,
        run_id: str = "",
        turn_id: str = "",
    ) -> None:
        # ``store`` is the dict the tool mutates; in production this is
        # ``context.extras`` so other components can read the current list.
        # Tests can pass an empty dict and inspect it directly.
        self._store: dict[str, Any] = store if store is not None else {}
        self._emit = emit
        self._run_id = run_id
        self._turn_id = turn_id

    async def run(self, **kwargs: Any) -> str:
        raw = kwargs.get("todos")
        if not isinstance(raw, list):
            return "Error: 'todos' must be an array."

        try:
            normalized = self._validate(raw)
        except _TodoValidationError as err:
            return f"Error: {err}"

        all_done = bool(normalized) and all(
            item["status"] == "completed" for item in normalized
        )
        new_list: list[dict[str, str]] = [] if all_done else normalized
        self._store["todos"] = new_list

        if self._emit is not None:
            event = TodosUpdated(
                run_id=self._run_id,
                turn_id=self._turn_id,
                todos=[dict(item) for item in normalized],
            )
            result = self._emit(event)
            if inspect.isawaitable(result):
                await result

        logger.info(
            "todo_write_tool_invoked count=%d in_progress=%d completed=%d",
            len(normalized),
            sum(1 for t in normalized if t["status"] == "in_progress"),
            sum(1 for t in normalized if t["status"] == "completed"),
        )

        if all_done:
            return (
                "All tasks completed; cleared the todo list. If new work "
                "appears, call TodoWrite again with a fresh list."
            )
        return (
            "Todos updated. Keep exactly one item in_progress while you work "
            "on it; mark it completed as soon as it's done."
        )

    def _validate(self, raw: list[Any]) -> list[dict[str, str]]:
        seen_in_progress = 0
        out: list[dict[str, str]] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise _TodoValidationError(f"todo[{index}] must be an object.")
            content = item.get("content")
            active_form = item.get("activeForm")
            status = item.get("status")
            if not isinstance(content, str) or not content.strip():
                raise _TodoValidationError(
                    f"todo[{index}].content must be a non-empty string."
                )
            if not isinstance(active_form, str) or not active_form.strip():
                raise _TodoValidationError(
                    f"todo[{index}].activeForm must be a non-empty string."
                )
            if not isinstance(status, str) or status not in _VALID_STATUSES:
                raise _TodoValidationError(
                    f"todo[{index}].status must be one of "
                    f"{sorted(_VALID_STATUSES)}; got {status!r}."
                )
            if status == "in_progress":
                seen_in_progress += 1
            out.append(
                {
                    "content": content.strip(),
                    "activeForm": active_form.strip(),
                    "status": status,
                }
            )
        if seen_in_progress > 1:
            raise _TodoValidationError(
                f"at most one todo may be in_progress at a time "
                f"(got {seen_in_progress})."
            )
        return out


class _TodoValidationError(ValueError):
    """Internal error for uniform error-string returns."""
