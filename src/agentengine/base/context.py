"""Mutable per-run context passed from the host application into the engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentengine.llm.interfaces import LLMClient
from agentengine.stream.printer import Printer
from agentengine.tools.collection import ToolCollection

if TYPE_CHECKING:
    from agentengine.enterprise.approval import ApprovalGate
    from agentengine.hooks.manager import HookManager
    from agentengine.persistence.port import PersistencePort
    from agentengine.runtime.cancellation import CancellationToken
    from agentengine.runtime.compaction import Compactor
    from agentengine.tools.policy import ExecPolicy


@dataclass
class AgentContext:
    request_id: str
    query: str
    llm: LLMClient | None = None
    printer: Printer | None = None
    tool_collection: ToolCollection = field(default_factory=ToolCollection)
    session_id: str = ""
    conversation_id: str = ""
    user: Any = None
    db: Any = None
    persistence: PersistencePort | None = None

    # Engine-owned cross-module dependencies.  These used to travel only via
    # ``extras``; keeping them explicit makes their contract type-checkable and
    # prevents a misspelled security/lifecycle key from silently disabling it.
    # The runtime still reads the legacy extras keys as a compatibility bridge
    # for existing host integrations.
    hooks: HookManager | None = None
    exec_policy: ExecPolicy | None = None
    approval_gate: ApprovalGate | None = None
    compactor: Compactor | None = None
    workspace_root: str = ""
    cancellation_token: CancellationToken | None = None
    disable_host_exec: bool = False

    extras: dict[str, Any] = field(default_factory=dict)
