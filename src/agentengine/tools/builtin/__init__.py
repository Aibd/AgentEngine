"""Built-in tools shipped with agentengine.

Each tool here is independently importable. ``BUILTIN_TOOL_FACTORIES`` lets
``RunConfig`` setup hooks register tools by name without re-importing each
module:

    from agentengine.tools.builtin import build_default_tools_for_context

    async def _setup(context):
        for tool in build_default_tools_for_context(context):
            context.tool_collection.add(tool)
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentengine.tools.base import Tool
from agentengine.tools.builtin.ask_user_question_tool import AskUserQuestionTool
from agentengine.tools.builtin.bash_tool import BashTool, is_destructive_command
from agentengine.tools.builtin.skill_resource_tool import ReadSkillResource
from agentengine.tools.builtin.skill_script_tool import RunSkillScript
from agentengine.tools.builtin.skill_tool import SkillTool
from agentengine.tools.builtin.todo_write_tool import TodoWriteTool
from agentengine.tools.builtin.web_fetch_tool import WebFetchTool
from agentengine.tools.builtin.web_search_tool import WebSearchTool
from agentengine.skills.loader import SkillLoader

if TYPE_CHECKING:
    from agentengine.base.context import AgentContext

__all__ = [
    "AskUserQuestionTool",
    "BashTool",
    "ReadSkillResource",
    "RunSkillScript",
    "SkillTool",
    "TodoWriteTool",
    "WebFetchTool",
    "WebSearchTool",
    "is_destructive_command",
    "BUILTIN_TOOL_FACTORIES",
    "build_default_tools",
    "build_default_tools_for_context",
]


# Map of well-known builtin tool names → factory. Each factory accepts a
# ``workspace_root`` plus optional kwargs and returns a fresh tool instance.
# Tools that don't care about workspace_root accept it and ignore it so the
# factory signatures stay uniform.
#
# Host-level file tools (read_file, write_file, edit_file, glob, grep) are
# intentionally omitted: in sandbox mode all file I/O goes through the sandbox
# container (bash/python tools). Direct host filesystem access from agents is
# not part of the SaaS chatbot model.
BUILTIN_TOOL_FACTORIES: dict[str, Callable[..., Tool]] = {
    "bash": lambda workspace_root, **kw: BashTool(workspace_root=workspace_root, **kw),
    "web_search": lambda workspace_root, **kw: WebSearchTool(**kw),
    "web_fetch": lambda workspace_root, **kw: WebFetchTool(**kw),
    "TodoWrite": lambda workspace_root, **kw: TodoWriteTool(**kw),
    "AskUserQuestion": lambda workspace_root, **kw: AskUserQuestionTool(**kw),
    "Skill": lambda workspace_root, **kw: SkillTool(kw.pop("loader", None) or SkillLoader()),
    "ReadSkillResource": lambda workspace_root, **kw: ReadSkillResource(
        kw.pop("loader", None) or SkillLoader(),
        enabled_names=kw.pop("enabled_names", None),
    ),
}

# Tools resolvable by explicit name but excluded from the implicit "build
# everything" default (when ``include is None``). ``Skill`` and
# ``ReadSkillResource`` depend on a SkillLoader and should only attach when a
# definition asks for them by name.
_DEFAULT_EXCLUDED: frozenset[str] = frozenset({"Skill", "ReadSkillResource"})


def _build(
    *,
    workspace_root: str | os.PathLike[str],
    include: list[str] | None,
    exclude: list[str] | None,
    per_tool_kwargs: dict[str, dict[str, Any]],
) -> list[Tool]:
    if include is not None:
        names = list(include)
    else:
        names = [n for n in BUILTIN_TOOL_FACTORIES if n not in _DEFAULT_EXCLUDED]
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
) -> list[Tool]:
    """Construct the standard set of builtin tools for an agent definition.

    Args:
        workspace_root: workspace root path; defaults to ``$AGENT_WORKSPACE_ROOT``
            or the current working directory.
        include: explicit list of tool names to include. If None, every tool
            in :data:`BUILTIN_TOOL_FACTORIES` is built.
        exclude: tool names to skip. Applied after ``include``.
    """
    if workspace_root is None:
        workspace_root = Path(os.getenv("AGENT_WORKSPACE_ROOT", os.getcwd()))
    return _build(
        workspace_root=workspace_root,
        include=include,
        exclude=exclude,
        per_tool_kwargs={},
    )


def build_default_tools_for_context(
    context: "AgentContext",
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[Tool]:
    """RunConfig-setup-friendly wrapper.

    Pulls ``workspace_root``, the runtime ``emit`` function, and the run/turn
    ids out of the context's ``extras`` (TurnRunner installs all of them before
    invoking the agent's setup hook). Falls back gracefully when called outside
    a TurnRunner — every dependency the tools take is optional.
    """
    workspace_root = context.extras.get("workspace_root") or Path(
        os.getenv("AGENT_WORKSPACE_ROOT", os.getcwd())
    )
    emit = context.extras.get("emit")
    run_id = context.extras.get("run_id", "") or ""
    turn_id = context.extras.get("turn_id", "") or ""
    sandbox_manager = context.extras.get("sandbox_manager")
    conversation_id = context.conversation_id or ""

    per_tool: dict[str, dict[str, Any]] = {}
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

    tools = _build(
        workspace_root=workspace_root,
        include=include,
        exclude=exclude,
        per_tool_kwargs=per_tool,
    )

    # When a sandbox manager is available, swap host-level BashTool for
    # SandboxedBashTool. SandboxedPythonTool has no host-level counterpart
    # and must be added explicitly by the caller (e.g. web_api).
    if sandbox_manager is not None and conversation_id:
        try:
            from agentengine.sandbox.tools import SandboxedBashTool  # noqa: F811
        except ImportError:
            pass
        else:
            tools = [
                SandboxedBashTool(manager=sandbox_manager, conversation_id=conversation_id)
                if isinstance(t, BashTool)
                else t
                for t in tools
            ]
    return tools
