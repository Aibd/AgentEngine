"""Built-in tools shipped with agentkit.

Each tool here is independently importable. ``BUILTIN_TOOL_FACTORIES`` lets
``AgentSpec`` setup hooks register tools by name without re-importing each
module:

    from agentkit.tools.builtin import build_default_tools_for_context

    async def _setup(context):
        for tool in build_default_tools_for_context(context):
            context.tool_collection.add(tool)
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentkit.tools.base import Tool
from agentkit.tools.builtin.ask_user_question_tool import AskUserQuestionTool
from agentkit.tools.builtin.bash_tool import BashTool, is_destructive_command
from agentkit.tools.builtin.file_edit_tool import FileEditTool
from agentkit.tools.builtin.file_write_tool import FileAccessTracker, FileWriteTool
from agentkit.tools.builtin.glob_tool import GlobTool
from agentkit.tools.builtin.grep_tool import GrepTool
from agentkit.tools.builtin.read_file_tool import ReadFileTool
from agentkit.tools.builtin.skill_tool import SkillTool
from agentkit.tools.builtin.todo_write_tool import TodoWriteTool

if TYPE_CHECKING:
    from agentkit.base.context import AgentContext

__all__ = [
    "AskUserQuestionTool",
    "BashTool",
    "FileEditTool",
    "FileWriteTool",
    "GlobTool",
    "GrepTool",
    "ReadFileTool",
    "SkillTool",
    "TodoWriteTool",
    "FileAccessTracker",
    "is_destructive_command",
    "BUILTIN_TOOL_FACTORIES",
    "build_default_tools",
    "build_default_tools_for_context",
]


# Map of well-known builtin tool names → factory. Each factory accepts a
# ``workspace_root`` plus optional kwargs and returns a fresh tool instance.
# Tools that don't care about workspace_root accept it and ignore it so the
# factory signatures stay uniform.
BUILTIN_TOOL_FACTORIES: dict[str, Callable[..., Tool]] = {
    "bash": lambda workspace_root, **kw: BashTool(workspace_root=workspace_root, **kw),
    "glob": lambda workspace_root, **kw: GlobTool(workspace_root=workspace_root, **kw),
    "grep": lambda workspace_root, **kw: GrepTool(workspace_root=workspace_root, **kw),
    "read_file": lambda workspace_root, **kw: ReadFileTool(workspace_root=workspace_root, **kw),
    "write_file": lambda workspace_root, **kw: FileWriteTool(workspace_root=workspace_root, **kw),
    "edit_file": lambda workspace_root, **kw: FileEditTool(workspace_root=workspace_root, **kw),
    "TodoWrite": lambda workspace_root, **kw: TodoWriteTool(**kw),
    "AskUserQuestion": lambda workspace_root, **kw: AskUserQuestionTool(**kw),
}


def _build(
    *,
    workspace_root: str | os.PathLike[str],
    include: list[str] | None,
    exclude: list[str] | None,
    per_tool_kwargs: dict[str, dict[str, Any]],
) -> list[Tool]:
    names = list(include) if include is not None else list(BUILTIN_TOOL_FACTORIES)
    excluded = set(exclude or [])
    tools: list[Tool] = []
    for name in names:
        if name in excluded:
            continue
        factory = BUILTIN_TOOL_FACTORIES.get(name)
        if factory is None:
            raise KeyError(f"Unknown builtin tool: {name}")
        tools.append(factory(workspace_root, **per_tool_kwargs.get(name, {})))
    return tools


def build_default_tools(
    workspace_root: str | os.PathLike[str] | None = None,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    access_tracker: FileAccessTracker | None = None,
) -> list[Tool]:
    """Construct the standard set of builtin tools for an agent spec.

    Args:
        workspace_root: workspace root path; defaults to ``$AGENT_WORKSPACE_ROOT``
            or the current working directory.
        include: explicit list of tool names to include. If None, every tool
            in :data:`BUILTIN_TOOL_FACTORIES` is built.
        exclude: tool names to skip. Applied after ``include``.
        access_tracker: shared :class:`FileAccessTracker` injected into
            write/edit tools so they can enforce the read-before-write rule.

    Note:
        TodoWrite and AskUserQuestion need an ``emit`` callback and run/turn
        ids to publish their runtime events. This factory leaves them at
        their no-op defaults; use :func:`build_default_tools_for_context`
        when you have a live ``AgentContext``.
    """
    if workspace_root is None:
        workspace_root = Path(os.getenv("AGENT_WORKSPACE_ROOT", os.getcwd()))
    per_tool: dict[str, dict[str, Any]] = {}
    if access_tracker is not None:
        per_tool["write_file"] = {"access_tracker": access_tracker}
        per_tool["edit_file"] = {"access_tracker": access_tracker}
    return _build(
        workspace_root=workspace_root,
        include=include,
        exclude=exclude,
        per_tool_kwargs=per_tool,
    )


def build_default_tools_for_context(
    context: "AgentContext",
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[Tool]:
    """AgentSpec-setup-friendly wrapper.

    Pulls ``workspace_root``, the per-run :class:`FileAccessTracker`, the
    runtime ``emit`` function, and the run/turn ids out of the context's
    ``extras`` (TurnRunner installs all of them before invoking the agent's
    setup hook). Falls back gracefully when called outside a TurnRunner —
    every dependency the tools take is optional, and they degrade to a
    no-op when missing.
    """
    workspace_root = context.extras.get("workspace_root") or Path(
        os.getenv("AGENT_WORKSPACE_ROOT", os.getcwd())
    )
    tracker = context.extras.get("file_access_tracker")
    emit = context.extras.get("emit")
    run_id = context.extras.get("run_id", "") or ""
    turn_id = context.extras.get("turn_id", "") or ""

    per_tool: dict[str, dict[str, Any]] = {}
    if tracker is not None:
        per_tool["write_file"] = {"access_tracker": tracker}
        per_tool["edit_file"] = {"access_tracker": tracker}
    per_tool["TodoWrite"] = {
        "store": context.extras,
        "emit": emit,
        "run_id": run_id,
        "turn_id": turn_id,
    }
    per_tool["AskUserQuestion"] = {
        "emit": emit,
        "run_id": run_id,
        "turn_id": turn_id,
    }
    return _build(
        workspace_root=workspace_root,
        include=include,
        exclude=exclude,
        per_tool_kwargs=per_tool,
    )
