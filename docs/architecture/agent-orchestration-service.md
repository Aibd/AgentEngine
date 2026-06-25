# AgentOrchestrationService 详解

AgentOrchestrationService 是 AgentEngine SDK 的上层服务封装，位于 `app/backend/services/agent_orchestration_service.py`。它在 `AgentEngine` 基础上注入进程级共享的持久化、并发锁与企业中间件，为 Web API 和 CLI 提供开箱即用的编排能力。

## 层级定位

```
接入层 (Web UI / CLI / Host)
  └─ Web API (FastAPI)
       └─ AgentOrchestrationService   ← 本文重点
            └─ AgentEngine (SDK)
                 └─ TurnRunner / run_turn()
```

`AgentEngine` 是纯粹的无状态 SDK 门面，不持有任何外部资源引用。`AgentOrchestrationService` 则承担"装配层"职责：将持久化、锁、中间件等依赖组装成一个可直接使用的服务实例。

## 类设计

```python
class AgentOrchestrationService(AgentEngine):
    def __init__(
        self,
        *,
        presets: Mapping[str, PresetLike] | None = None,
        llm_factory: Callable[[], LLMClient | None] | None = None,
        require_llm: bool = False,
        persistence: PersistencePort | None = None,
        lock_manager: ConversationLockManager | None = None,
        middleware: MiddlewareChain | None = None,
    ) -> None:
        super().__init__(
            definitions=presets or AGENT_REGISTRY,
            llm_factory=llm_factory or _default_llm_factory,
            require_llm=require_llm,
            persistence=persistence,
            lock_manager=lock_manager,
            middleware=middleware,
        )
```

关键设计决策：

- **继承而非组合**：直接 extends `AgentEngine`，不引入额外抽象层。这意味着所有 `AgentEngine` 的公共接口（`run()`、`interrupt()`、`create_streaming_context()`、`close()`）对调用方完全透明。
- **默认值覆盖**：未显式传入 `presets` 时，自动注册 `app/backend/agents/REGISTRY` 中的全部 AgentDefinition（general_chat、deep_research 等 10+ 个预设代理）。
- **零硬编码 LLM 依赖**：默认 LLM 工厂 `_default_llm_factory` 通过 `create_llm_from_env(required=False)` 读取环境变量，未配置时静默退化为 `None`。

## 在 Web API 中的实例化方式

```python
# web_api.py — 每次流式请求创建一个新的 service 实例
service = AgentOrchestrationService(
    persistence=PERSISTENCE,       # 进程级共享 SqlitePersistence
    lock_manager=LOCK_MANAGER,     # 进程级共享 InMemoryConversationLockManager
    middleware=_build_enterprise_middleware(),
)
```

### 为什么"每次请求创建一个新实例"？

1. **中间件是无状态的**：`MiddlewareChain` 只是一个函数列表，其依赖（`QuotaStore`、`ApprovalGate`）是进程级单例。
2. **LLM 客户端生命周期**：每次运行可能使用不同的 LLM 配置，`llm_factory` 在 `run()` 内部惰性调用，由 `AgentEngine` 管理其生命周期并通过 `close()` 清理。
3. **避免状态泄漏**：`AgentEngine._active_runs` 是请求级别的簿记，不应跨请求共享。

### 哪些是真正的进程级单例？

| 组件 | 实例 | 原因 |
|---|---|---|
| `SqlitePersistence` | 1 个 | 所有对话写入同一数据库，WAL 模式支持并发读 |
| `InMemoryConversationLockManager` | 1 个 | 必须同一个锁表才能按 conversation_id 串行化 |
| `QuotaStore` | 1 个 | 跨请求累计配额消耗 |
| `ApprovalGate` | 1 个 | 挂起的审批请求需要在所有请求中可见 |
| `SkillRegistry` | 1 个 | 技能安装/启禁状态全局一致 |
| `SandboxManager` | 1 个或 0 个 | 容器生命周期独立于 HTTP 请求 |

## 执行流程

以 Web API 的流式运行请求为例：

```
GET /api/runs/stream?query=...&agent_name=deep_research&conversation_id=abc

  _run_agent_events()
    │
    ├─ service = AgentOrchestrationService(
    │      persistence=PERSISTENCE,
    │      lock_manager=LOCK_MANAGER,
    │      middleware=MiddlewareChain([tracing, quota, retry, approval])
    │  )
    │
    ├─ context, event_stream = service.create_streaming_context(
    │      request_id="web-xxx",
    │      query="用户问题",
    │      conversation_id="default:abc",
    │  )
    │
    ├─ 装配工具链:
    │    • SkillTool + ReadSkillResource (仅已启用的技能)
    │    • SandboxedBashTool / SandboxedPythonTool (沙盒模式)
    │    • 注入 ExecPolicy 作为安全兜底
    │
    ├─ asyncio.create_task(service.run(...))
    │    │
    │    └─ AgentEngine.run()
    │         ├─ _resolve_config("deep_research") → RunConfig
    │         ├─ context.llm = llm_factory() → OpenAICompatibleClient
    │         ├─ AgentRun(config, context)
    │         ├─ TurnRunner.run()
    │         │    ├─ lock_manager.acquire(conversation_id)  ← 串行化
    │         │    └─ run_turn()                            ← ReAct 循环
    │         └─ emit RuntimeEvent → Printer → SSE
    │
    └─ async for event in event_stream:
         yield SSE frame to client
```

## 中间件洋葱层

```python
def _build_enterprise_middleware() -> MiddlewareChain:
    return MiddlewareChain([
        otel_tracing_middleware(),   # 1. 最外层 — OTel span
        quota_middleware(store),     # 2. 配额检查与扣减
        retry_middleware(),          # 3. LLM 瞬态错误重试
        approval_middleware(gate),   # 4. 破坏性工具人工审批
    ])
```

调用顺序（从外到内）：tracing → quota → retry → approval → 实际 `run_turn()`

每个中间件签名统一为：

```python
async def middleware(
    ctx: MiddlewareContext,
    next: Callable[[MiddlewareContext], Awaitable[str]],
) -> str:
    # 前置处理
    result = await next(ctx)
    # 后置处理
    return result
```

## 并发模型

```
请求 A (conversation_id="abc")
  获取 lock("abc") ──────── run_turn() ──────── 释放 lock("abc")

请求 B (conversation_id="abc")
  等待 lock("abc") ──────── 排队... ──────── 获取锁 ──────── run_turn()

请求 C (conversation_id="xyz")
  获取 lock("xyz") ──────── 并行执行（不受 A/B 影响）
```

- 同一对话的多次请求严格串行，防止消息乱序和状态竞争。
- 不同对话完全并行，无相互阻塞。
- 锁实现为 `asyncio.Lock`（进程内）或 Redis SETNX（多副本）。

## 生命周期管理

```python
# 创建
service = AgentOrchestrationService(...)

# 运行（可多次，每次一个 agent invocation）
result = await service.run(agent_name="deep_research", query="...")

# 流式运行
context, stream = service.create_streaming_context(...)
task = asyncio.create_task(service.run(agent_name=..., query=..., context=context))
async for event in stream:
    yield event

# 中断
service.interrupt(request_id, reason="user_cancelled")

# 清理（关闭 LLM 客户端连接）
await service.close()
```

`close()` 负责清理 `AgentEngine` 内部管理的 LLM 客户端 `httpx.AsyncClient`，`Shutdown()` 则完全销毁所有资源。在 Web API 的 `finally` 块中确保 `close()` 始终被调用。

## 容器沙盒集成

当 `SandboxManager` 可用时，服务自动将危险工具（bash、python、RunSkillScript）替换为沙盒版本：

```
SandboxManager 可用?
  ├─ Yes → SandboxedBashTool / SandboxedPythonTool / RunSkillScript(sandbox)
  └─ No  → 降级到宿主机 BashTool（兼容无 Docker 环境）
```

沙盒生命周期：
- **获取**：会话首次运行时创建容器，后续请求复用同一容器（维持工作区状态）。
- **释放**：不随单次请求结束而销毁——由 `SandboxManager.reap_idle()` 异步清理空闲容器。
- **隔离**：不同 conversation_id 获得独立容器和独立工作区。

## 租户隔离

Web API 通过 `tenant_id` 参数实现轻量级多租户：

```python
scoped_conversation_id = f"{tenant_id}:{conversation_id}"  # 非 default 租户
```

这影响三个层面：
1. **锁粒度**：`lock_manager.acquire(scoped_conversation_id)` — 不同租户的同名 conversation 不互相阻塞。
2. **持久化**：消息按 `scoped_conversation_id` 存入 SQLite。
3. **配额**：`QuotaStore` 按 `tenant_id` 独立计费。

## 技能编排

`AgentOrchestrationService` 不直接管理技能——这由 `SkillRegistry`（进程单例）负责。但服务在运行装配阶段将技能桥接到工具链：

```
SkillRegistry.enabled_names()
  → SkillTool(enabled_names=...)
  → ReadSkillResource(enabled_names=...)
  → RunSkillScript(enabled_names=...)   # 仅在沙盒可用时
```

当用户在请求中指定 `skill` 参数时，服务自动向查询添加指令前缀，引导 LLM 先调用对应技能：

```
原始查询: "分析这份数据"
处理后的查询: "请先调用 Skill 工具（skill=\"data_analysis\"）加载该技能，并严格按其说明处理以下请求：\n\n分析这份数据"
```

## 与 AgentEngine 的边界

| 职责 | AgentEngine (SDK) | AgentOrchestrationService |
|---|---|---|
| 代理定义注册 | 需外部传入 | 自动注入 AGENT_REGISTRY |
| LLM 配置 | 需外部传入 | 自动读取环境变量 |
| 依赖注入 | 完全由调用方控制 | 预设合理默认值 |
| 进程单例管理 | 不参与 | 不参与（由 Web API 层管理） |
| 可独立使用 | 是（零依赖 SDK） | 否（依赖 app.backend 约定） |

AgentOrchestrationService 本质上是"约定优于配置"的具体化，将 AgentEngine SDK 适配到 AgentEngine Web 应用的上下文中。
