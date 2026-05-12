from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable


class ExecPolicyAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True, slots=True)
class ExecPolicyRule:
    """Prefix rule for tool execution.

    ``prefix`` is matched against a command-like subject. For shell tools this
    is the ``command`` argument; for other tools it is the tool name.
    """

    prefix: str | tuple[str, ...]
    action: ExecPolicyAction
    reason: str = ""

    def matches(self, subject: str) -> bool:
        if isinstance(self.prefix, tuple):
            return any(subject.startswith(item) for item in self.prefix)
        return subject.startswith(self.prefix)


@dataclass(frozen=True, slots=True)
class ExecPolicyDecision:
    action: ExecPolicyAction
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.action is ExecPolicyAction.ALLOW


class ExecPolicy:
    """Allow/Deny/Ask policy evaluated immediately before a tool runs."""

    def __init__(
        self,
        rules: Iterable[ExecPolicyRule] | None = None,
        *,
        default: ExecPolicyAction = ExecPolicyAction.ALLOW,
    ) -> None:
        self.rules = list(rules or [])
        self.default = default

    def decide(self, tool_name: str, arguments: dict[str, Any]) -> ExecPolicyDecision:
        subject = _subject(tool_name, arguments)
        for rule in self.rules:
            if rule.matches(subject):
                return ExecPolicyDecision(rule.action, rule.reason)
        return ExecPolicyDecision(self.default)


def _subject(tool_name: str, arguments: dict[str, Any]) -> str:
    command = arguments.get("command")
    if isinstance(command, str) and command.strip():
        return command.strip()
    return tool_name


__all__ = [
    "ExecPolicy",
    "ExecPolicyAction",
    "ExecPolicyDecision",
    "ExecPolicyRule",
]
