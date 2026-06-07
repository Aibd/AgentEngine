"""Example preset: a code agent whose bash/python run inside a sandbox.

This shows the *only* integration seam you need. Instead of adding the
in-process ``BashTool``, the ``setup`` hook adds ``SandboxedBashTool`` /
``SandboxedPythonTool`` bound to a process-wide ``SandboxManager`` and the
current ``conversation_id``. Everything above the tool boundary is unchanged.

Two things the host must provide before a run reaches this hook:
  * ``context.extras["sandbox_manager"]`` — the shared SandboxManager singleton
  * ``context.conversation_id``           — the isolation key

The host should also point the file tools at the same workspace the container
mounts, so bash/python and read_file/write_file share one filesystem:

    host_ws = manager.host_workspace_for(conversation_id)
    context.extras["workspace_root"] = host_ws

See docs/sandbox-deployment.md for the full host wiring + container release.
"""

from __future__ import annotations

from agentengine.base.context import AgentContext
from agentengine.sandbox import SandboxedBashTool, SandboxedPythonTool

# AgentPreset import path depends on your layout; here it lives under the
# app backend tree (app/backend/agents). Adjust if you copy this elsewhere.
from app.backend.agents.preset import AgentPreset

_INSTRUCTIONS = (
    "You are a coding assistant that runs code to answer questions.\n"
    "- Use the `python` tool to execute Python and `bash` for shell work.\n"
    "- Files you create under /workspace persist across calls in this session.\n"
    "- The environment has no network access; do not attempt to fetch URLs.\n"
    "- Prefer small, verifiable steps; show the code you ran."
)


async def _setup(context: AgentContext) -> None:
    if context.tool_collection is None:
        return
    manager = context.extras.get("sandbox_manager")
    if manager is None:
        raise RuntimeError(
            "sandboxed_coder requires context.extras['sandbox_manager']; "
            "construct a SandboxManager and inject it before run()."
        )
    conv = context.conversation_id
    if not conv:
        raise RuntimeError("sandboxed_coder requires a non-empty conversation_id")

    # Same names ("bash", "python") → transparent to ToolExecutor and the LLM.
    if context.tool_collection.get("bash") is None:
        context.tool_collection.add(SandboxedBashTool(manager=manager, conversation_id=conv))
    if context.tool_collection.get("python") is None:
        context.tool_collection.add(SandboxedPythonTool(manager=manager, conversation_id=conv))


PRESET = AgentPreset(
    name="sandboxed_coder",
    description="Code agent whose bash/python execute inside a per-conversation sandbox.",
    instructions=_INSTRUCTIONS,
    setup=_setup,
)
