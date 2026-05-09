# errors — 错误体系

> `src/agentengine/errors.py` 定义了框架内所有异常的基类和子类，支持结构化序列化、可重试标记、HTTP 状态码映射。

---

## 设计意图

传统 Python 异常的问题是：
- 信息散落在 `args` 中，难以结构化
- 不知道是否该重试
- 传给前端时需要手动翻译

AgentEngine 的错误体系解决这些问题：
- **统一基类**：所有框架异常继承 `AgentEngineError`
- **结构化**：`to_dict()` 输出标准 JSON
- **可重试标记**：`retryable=True/False` 指导调用方
- **分类**：`category` 字段用于监控分组

---

## 类层次

```
Exception
└── AgentEngineError
    ├── LLMError
    │   ├── LLMHTTPError
    │   │   └── LLMRateLimitError
    │   ├── LLMTimeoutError
    │   ├── LLMConnectionError
    │   ├── LLMStreamError
    │   └── LLMContextWindowError
    ├── ToolExecutionError
    └── RuntimeExecutionError
        └── AgentCancelledError

ApprovalDeniedError (AgentEngineError 子类)
QuotaExceededError (AgentEngineError 子类)
```

---

## AgentEngineError — 基类

```python
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
        ...

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.error_code,
            "message": self.message,
            "category": self.error_category,
            "retryable": self.is_retryable,
            "status_code": self.error_status_code,
            "details": self.details,
        }
```

---

## 子类速查

| 异常 | code | category | retryable | 典型场景 |
|------|------|----------|-----------|---------|
| `AgentEngineError` | `agentengine_error` | `agentengine` | ❌ | 通用基类 |
| `LLMError` | `llm_error` | `llm` | ❌ | LLM 层通用错误 |
| `LLMHTTPError` | `llm_http_error` | `llm` | 视状态码 | HTTP 非 2xx |
| `LLMRateLimitError` | `llm_rate_limited` | `llm` | ✅ | 429 限流 |
| `LLMTimeoutError` | `llm_timeout` | `llm` | ✅ | 请求超时 |
| `LLMConnectionError` | `llm_connection_error` | `llm` | ✅ | 网络断开 |
| `LLMStreamError` | `llm_stream_error` | `llm` | ✅ | 流解析失败 |
| `LLMContextWindowError` | `llm_context_window_exceeded` | `llm` | ❌ | 上下文超长 |
| `ToolExecutionError` | `tool_execution_error` | `tool` | ❌ | 工具执行失败 |
| `RuntimeExecutionError` | `runtime_execution_error` | `runtime` | ❌ | 运行时通用错误 |
| `AgentCancelledError` | `agent_cancelled` | `runtime` | ❌ | 运行被取消 |
| `ApprovalDeniedError` | `approval_denied` | `approval` | ❌ | 审批被拒绝 |
| `QuotaExceededError` | `quota_exceeded` | `quota` | ❌ | 配额超限 |

---

## 错误序列化

### to_dict()

```python
try:
    await service.run(...)
except AgentEngineError as e:
    payload = e.to_dict()
    # {
    #   "code": "llm_timeout",
    #   "message": "provider timed out",
    #   "category": "llm",
    #   "retryable": True,
    #   "details": {}
    # }
```

### error_to_dict() — 通用转换

```python
from agentengine.errors import error_to_dict

# 对任意异常都有效
try:
    ...
except Exception as e:
    payload = error_to_dict(e)
    # 如果是 AgentEngineError → 调用 to_dict()
    # 否则 → 生成通用 unexpected_error 结构
```

---

## 前端错误映射

框架在 Python 端和 Web 前端各维护一份「错误码 → 用户友好语」表：

- Python: `scripts/renderers/friendly_errors.py`
- TypeScript: `web/src/friendlyErrors.ts`

**加新错误码时必须两边一起改**，保证用户体验一致。

---

## 在 run_turn 中的处理

```python
try:
    result = await _loop(...)
except Exception as exc:
    agent.state = AgentState.ERROR
    reason = TurnRunner._classify(exc)  # → TerminalReason
    emit(RunFailed(
        error_type=type(exc).__name__,
        error_message=str(exc),
        terminal_reason=reason.value,
        error_payload=error_to_dict(exc),
    ))
    raise
```

`RunFailed.error_payload` 携带结构化错误信息，前端可以据此渲染不同的错误卡片。

---

## 关联文档

- [runtime.md](runtime.md) — TurnRunner 如何分类错误
- [enterprise.md](enterprise.md) — ApprovalDeniedError、QuotaExceededError
- [stream.md](stream.md) — Printer 如何将错误转成 SSE error 事件
