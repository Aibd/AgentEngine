"""Named legacy ``AgentContext.extras`` keys.

New engine-owned dependencies belong on :class:`AgentContext` as typed fields.
These constants cover the remaining compatibility bridge and per-run outputs;
host applications should not depend on keys prefixed with an underscore.
"""
from __future__ import annotations

from typing import Final

HOOKS: Final = "hooks"
EXEC_POLICY: Final = "exec_policy"
APPROVAL_GATE: Final = "approval_gate"
COMPACTOR: Final = "compactor"
WORKSPACE_ROOT: Final = "workspace_root"
CANCELLATION_TOKEN: Final = "cancellation_token"
DISABLE_HOST_EXEC: Final = "disable_host_exec"
SANDBOX_MANAGER: Final = "sandbox_manager"
TENANT: Final = "tenant"
SECRETS: Final = "secrets"
PUBLIC_CONVERSATION_ID: Final = "public_conversation_id"

RUN_ID: Final = "run_id"
TURN_ID: Final = "turn_id"
RUNTIME_EVENTS: Final = "runtime_events"
RUN_STATE: Final = "run_state"
TERMINAL_REASON: Final = "terminal_reason"
EMIT: Final = "emit"
RUN_EVENT_LOG_PATH: Final = "run_event_log_path"

__all__ = [
    "APPROVAL_GATE", "CANCELLATION_TOKEN", "COMPACTOR", "DISABLE_HOST_EXEC",
    "EMIT", "EXEC_POLICY", "HOOKS", "PUBLIC_CONVERSATION_ID", "RUN_EVENT_LOG_PATH",
    "RUN_ID", "RUN_STATE", "RUNTIME_EVENTS", "SANDBOX_MANAGER", "SECRETS",
    "TENANT", "TERMINAL_REASON", "TURN_ID", "WORKSPACE_ROOT",
]
