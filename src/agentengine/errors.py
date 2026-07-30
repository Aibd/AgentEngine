from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    code: str
    message: str
    category: str = "agentengine"
    retryable: bool = False
    status_code: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "category": self.category,
            "retryable": self.retryable,
        }
        if self.status_code is not None:
            data["status_code"] = self.status_code
        if self.details:
            data["details"] = self.details
        return data


class AgentEngineError(Exception):
    code = "agentengine_error"
    category = "agentengine"
    retryable = False
    status_code: int | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        category: str | None = None,
        retryable: bool | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.__name__
        self.error_code = code or self.code
        self.error_category = category or self.category
        self.is_retryable = self.retryable if retryable is None else retryable
        self.error_status_code = self.status_code if status_code is None else status_code
        self.details = details or {}
        super().__init__(self.message)

    @property
    def info(self) -> ErrorInfo:
        return ErrorInfo(
            code=self.error_code,
            message=self.message,
            category=self.error_category,
            retryable=self.is_retryable,
            status_code=self.error_status_code,
            details=self.details,
        )

    def to_dict(self) -> dict[str, Any]:
        return self.info.to_dict()


class LLMError(AgentEngineError):
    code = "llm_error"
    category = "llm"


class LLMHTTPError(LLMError):
    code = "llm_http_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        body: str = "",
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged_details = dict(details or {})
        if body:
            merged_details["body"] = body
        super().__init__(
            message,
            retryable=self.retryable if retryable is None else retryable,
            status_code=status_code,
            details=merged_details,
        )


class LLMRateLimitError(LLMHTTPError):
    code = "llm_rate_limited"
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 429,
        body: str = "",
        retry_after_seconds: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged_details = dict(details or {})
        if retry_after_seconds is not None:
            merged_details["retry_after_seconds"] = retry_after_seconds
        super().__init__(
            message,
            status_code=status_code,
            body=body,
            retryable=True,
            details=merged_details,
        )


class LLMTimeoutError(LLMError):
    code = "llm_timeout"
    retryable = True


class LLMConnectionError(LLMError):
    code = "llm_connection_error"
    retryable = True


class LLMStreamError(LLMError):
    code = "llm_stream_error"
    retryable = True


class ContextWindowExceededError(LLMError):
    code = "llm_context_window_exceeded"
    retryable = False


class LLMContextWindowError(ContextWindowExceededError):
    """Backward-compatible name for context-window failures."""


class UsageLimitReachedError(LLMError):
    code = "llm_usage_limit_reached"
    retryable = False


class ToolExecutionError(AgentEngineError):
    code = "tool_execution_error"
    category = "tool"


class RuntimeExecutionError(AgentEngineError):
    code = "runtime_execution_error"
    category = "runtime"


class MaxStepsExceededError(RuntimeExecutionError):
    code = "max_steps_exceeded"


class AgentCancelledError(RuntimeExecutionError):
    code = "agent_cancelled"


def error_to_dict(error: BaseException) -> dict[str, Any]:
    if isinstance(error, AgentEngineError):
        return error.to_dict()
    return ErrorInfo(
        code="unexpected_error",
        message=str(error) or error.__class__.__name__,
        category="unexpected",
        retryable=False,
        details={"type": error.__class__.__name__},
    ).to_dict()
