"""Hook system: lifecycle event interception for agent runs.

Modeled after codex-rs `hooks/`, trimmed to fit the Python runtime:

- 5 event types (PreToolUse, PostToolUse, SessionStart, UserPromptSubmit, Stop)
- handlers are async Python callables, not shell commands
- per-event handlers run sequentially in registration order
- a handler can return ``HookResult.fail_abort()`` to stop the chain and
  signal the dispatch site to abort the in-progress operation
- exceptions from a handler default to ``FailedContinue`` so a buggy
  third-party hook can't kill the run
"""

from agentengine.hooks.manager import HookManager, get_default_manager
from agentengine.hooks.types import (
    HookAbortError,
    HookEvent,
    HookFn,
    HookOutcome,
    HookResult,
    PostToolUsePayload,
    PreToolUsePayload,
    SessionStartPayload,
    StopPayload,
    UserPromptSubmitPayload,
)

__all__ = [
    "HookEvent",
    "HookFn",
    "HookManager",
    "HookOutcome",
    "HookResult",
    "HookAbortError",
    "PreToolUsePayload",
    "PostToolUsePayload",
    "SessionStartPayload",
    "StopPayload",
    "UserPromptSubmitPayload",
    "get_default_manager",
]
