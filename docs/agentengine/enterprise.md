# enterprise — 企业级特性

> `src/agentengine/enterprise/` 提供生产环境所需的横切关注点：审批门、配额限制、中间件链、租户隔离、链路追踪和密钥管理。

---

## 模块组成

```
enterprise/
├── approval.py      # ApprovalGate — 破坏性工具审批
├── quota.py         # QuotaStore — 租户级配额
├── middleware.py    # MiddlewareChain — 洋葱式中间件
├── tenant.py        # TenantContext — 租户上下文
├── retry.py         # RetryMiddleware
├── tracing.py       # 分布式链路追踪
├── secrets.py       # 密钥管理
└── __init__.py
```

---

## ApprovalGate — 人工审批

**文件：** `src/agentengine/enterprise/approval.py`

### 设计意图

Agent 可能调用破坏性工具（`rm`, `git push --force`, `file_write` 覆盖重要文件）。`ApprovalGate` 在执行前暂停，等待人类审批。

```python
class ApprovalGate:
    def __init__(self, timeout_seconds: float = 300.0):
        self._pending: dict[str, asyncio.Future[ApprovalResult]] = {}
        self._timeout = timeout_seconds
```

### 工作流程

```
run_turn 遇到 is_destructive=True 的工具
    │
    ├──► emit ApprovalRequired(status="pending")
    ├──► await approval_gate.request_approval(tool_name, args, approval_id=...)
    │      │
    │      ├──► 创建 Future，存入 _pending
    │      ├──► 等待外部 resolve()
    │      └──► 超时 → ApprovalDeniedError
    │
    ├──► 外部系统调用 gate.resolve(approval_id, ApprovalResult(approved=True))
    │      │
    │      └──► Future.set_result() → 继续执行
    │
    └──► 如果 denied → ApprovalDeniedError → 工具跳过，记录失败
```

### 使用

```python
from agentengine.enterprise.approval import ApprovalGate, approval_middleware

gate = ApprovalGate(timeout_seconds=300)
mw = MiddlewareChain([approval_middleware(gate)])
service = AgentOrchestrationService(middleware=mw)

# 外部审批系统
@app.post("/approvals/{approval_id}")
def approve(approval_id: str, approved: bool):
    gate.resolve(approval_id, ApprovalResult(approved=approved))
```

---

## QuotaStore — 配额限制

**文件：** `src/agentengine/enterprise/quota.py`

### 设计意图

多租户场景下限制资源消耗：运行次数、工具调用次数、Token 用量。

```python
@dataclass
class QuotaLimits:
    max_runs: int = 0           # 0 = 不限
    max_tool_calls: int = 0
    max_tokens_in: int = 0
    max_tokens_out: int = 0
    window_seconds: float = 60.0  # 滑动窗口
```

### 配额维度

| 维度 | 检查时机 | 超限行为 |
|------|---------|---------|
| `runs` | TurnRunner.run() 开始前 | 拒绝运行 |
| `tool_calls` | 每次工具调用前 | 跳过该工具，记录失败 |
| `tokens_in` / `tokens_out` | UsageReport 后 | 在 record_tokens 中检查 |

### 使用

```python
from agentengine.enterprise.quota import QuotaStore, QuotaLimits, quota_middleware

store = QuotaStore()
store.set_limits("acme", QuotaLimits(max_runs=5, max_tool_calls=20, window_seconds=3600))

mw = MiddlewareChain([quota_middleware(store)])
service = AgentOrchestrationService(middleware=mw)
```

### 可插拔存储

`QuotaStore` 默认内存实现。 subclass 可对接 Redis、Postgres：

```python
class RedisQuotaStore(QuotaStore):
    async def _ensure_usage(self, tenant_id: str) -> QuotaUsage:
        # 从 Redis 读取
        ...
```

---

## MiddlewareChain — 洋葱式中间件

**文件：** `src/agentengine/enterprise/middleware.py`

### 设计意图

将横切关注点（租户隔离、配额、重试、追踪）从核心循环中剥离，通过可组合的中间件链实现。

```python
@dataclass
class MiddlewareContext:
    spec: AgentSpec
    run_context: AgentContext
    query: str
    extras: dict[str, Any]

MiddlewareFn = Callable[
    [MiddlewareContext, Callable[[MiddlewareContext], Awaitable[str]]],
    Awaitable[str],
]
```

### 组合方式

```python
chain = MiddlewareChain([
    tenant_isolation_middleware,
    quota_middleware(store),
    retry_middleware(max_retries=3),
    tracing_middleware,
])

result = await chain.run(spec, context, query, inner=run_turn)
```

执行顺序（洋葱模型）：

```
TenantIsolation
    ┌─ Quota
    │   ┌─ Retry
    │   │   ┌─ Tracing
    │   │   │   ┌─ run_turn()
    │   │   │   └─ return result
    │   │   └─ record trace
    │   └─ record tokens
    └─ clean tenant
return result
```

---

## TenantContext — 租户隔离

**文件：** `src/agentengine/enterprise/tenant.py`

```python
@dataclass
class TenantContext:
    tenant_id: str
    tenant_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
```

通过 `context.extras["tenant"]` 注入，中间件和配额系统据此区分租户。

---

## retry.py — 重试中间件

自动重试可恢复的错误（`LLMRateLimitError`, `LLMTimeoutError` 等），支持指数退避。

```python
mw = MiddlewareChain([retry_middleware(max_retries=3, base_delay=1.0)])
```

---

## tracing.py — 链路追踪

预留分布式追踪接口，可对接 Jaeger、Zipkin、OpenTelemetry。

```python
# 在 MiddlewareChain 中加入 tracing_middleware
# 自动为每次 run 创建 span，注入 run_id / turn_id 等标签
```

---

## secrets.py — 密钥管理

预留密钥管理抽象，支持从环境变量、KMS、Vault 读取敏感配置。

---

## 企业特性启用检查清单

| 特性 | 需要配置 |
|------|---------|
| 人工审批 | `ApprovalGate` + 审批 API 端点 |
| 配额限制 | `QuotaStore` + `QuotaLimits` + 租户识别 |
| 租户隔离 | `TenantContext` + 租户中间件 |
| 自动重试 | `retry_middleware` |
| 链路追踪 | `tracing_middleware` + OTel Collector |

---

## 关联文档

- [runtime.md](runtime.md) — TurnRunner 如何执行中间件链
- [hooks.md](hooks.md) — Hook 与中间件的对比：Hook 用于拦截，中间件用于包装
