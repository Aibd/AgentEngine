# AgentEngine 集成指南

AgentEngine 的定位是被业务系统嵌入的 Agent 运行引擎。业务系统负责认证、鉴权、租户路由、HTTP/WebSocket/gRPC 端点和配置中心；AgentEngine 负责 LLM 调用、ReAct 循环、工具调度、事件流、持久化端口和会话级锁。

## 最小集成

```python
from agentengine import AgentContext, AgentEngine, AgentPreset
from your_app.llm import llm_client  # 你的 LLM 客户端（需实现 chat/chat_stream 方法）

# ── 1. 创建引擎 ──────────────────────────────────────────────
engine = AgentEngine(
    presets={
        "support": AgentPreset(
            name="support",                                    # Agent 名称，run() 时引用
            instructions="You are a concise support assistant.", # 系统提示词
            auto_compact_tokens=120_000,                       # 超过阈值时自动压缩历史
        )
    }
)

# ── 2. 创建上下文 ────────────────────────────────────────────
context = AgentContext(
    request_id="req-123",              # 请求唯一标识，用于日志追踪
    query="帮我查一下订单状态",           # 用户输入的问题
    llm=llm_client,                    # LLM 客户端实例
    user=current_user,                 # 当前用户对象（可选，业务系统自行解析）
    conversation_id="conv-456",        # 会话 ID，用于多轮对话和历史加载
)

# ── 3. 执行 Agent ────────────────────────────────────────────
answer = await engine.run(
    agent_name="support",              # 使用哪个 Agent（对应 presets 中的 key）
    query=context.query,               # 用户问题
    context=context,                   # 传入上下文（包含 LLM、用户、会话等）
)
# answer 是最终回答字符串
```

## 注入 LLM

核心 SDK 不会读取环境变量。业务系统可以直接把 LLM 放进 `AgentContext`，也可以给 `AgentEngine` 一个工厂：

```python
# 方式 A：通过工厂函数注入（推荐）
# 每次 run() 时自动调用工厂创建 LLM 实例
engine = AgentEngine(
    presets={"support": AgentPreset(name="support")},
    llm_factory=lambda: app_container.llm_client(),  # 从你的 DI 容器获取
)

# 方式 B：直接注入到 context（见上方最小集成示例）
# context = AgentContext(..., llm=llm_client)
```

`LLMClient` 需要实现以下接口：

```python
# 非流式调用（返回完整响应）
async def chat(messages, *, tools=None, stream=False, **kwargs): ...

# 流式调用（返回 AsyncIterator，逐步输出 token）
async def chat_stream(messages, *, tools=None, **kwargs): ...
```

项目自带 `agentengine.llm.OpenAICompatibleClient` 和 `create_llm_from_env()`，但环境变量工厂只适合脚本或 reference app，不是核心 SDK 默认行为。

## 注入持久化

业务系统实现 `PersistencePort` 后注入：

```python
# 注入持久化实现（如 PostgreSQL、MySQL、MongoDB 等）
engine = AgentEngine(
    presets=presets,
    persistence=PostgresPersistence(pool),  # 你的持久化实现
)

context = AgentContext(
    request_id=request_id,
    query=query,
    llm=llm,
    conversation_id=conversation_id,  # 非空时启用历史加载和保存
)
```

当 `conversation_id` 非空时，引擎会在 run 前加载历史消息，结束后保存消息。

## 注入工具

工具通过 `AgentContext.tool_collection` 显式注入：

```python
from agentengine import AgentContext, Tool, ToolCollection

# ── 1. 定义工具 ──────────────────────────────────────────────
class OrderLookupTool(Tool):
    name = "lookup_order"                           # 工具名称，LLM 调用时引用
    description = "Look up an order by id."         # 工具描述，告诉 LLM 这个工具做什么
    schema = {                                      # JSON Schema，定义参数格式
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    }

    async def run(self, **kwargs):                  # 工具执行逻辑
        return await order_service.lookup(kwargs["order_id"])

# ── 2. 注入到上下文 ──────────────────────────────────────────
context = AgentContext(
    request_id=request_id,
    query=query,
    llm=llm,
    tool_collection=ToolCollection([OrderLookupTool()]),  # 把工具放入集合
)
```

`SkillTool` 也需要显式注册。`AgentContext` 不会自动给所有 Agent 加 Skill 能力。

## 会话锁

单进程可以使用默认 `InMemoryConversationLockManager`。多副本部署必须注入分布式锁：

```python
from agentengine import RedisConversationLockManager

# 注入 Redis 分布式锁（多副本部署必需）
engine = AgentEngine(
    presets=presets,
    lock_manager=RedisConversationLockManager(redis_client),  # 传入 Redis 客户端
)
```

Redis 版本是参考实现，使用 `SET NX PX` 获取锁，并用 compare-and-delete 脚本释放锁。生产环境需要根据业务运行时长配置合理 TTL。

## Middleware

`agentengine.enterprise` 是可选中间件集合，默认关闭。业务系统按需启用：

```python
from agentengine.enterprise import MiddlewareChain, QuotaStore, quota_middleware

# ── 1. 创建配额存储 ──────────────────────────────────────────
quota_store = QuotaStore()

# ── 2. 组装中间件链 ──────────────────────────────────────────
# 中间件按洋葱模型执行：外层先执行前置逻辑，内层先执行后置逻辑
middleware = MiddlewareChain([
    quota_middleware(quota_store),  # 配额限制（可选）
    # approval_middleware(...),     # 审批（可选）
    # otel_tracing_middleware(),    # 追踪（可选）
    # retry_middleware(),           # 重试（可选）
])

# ── 3. 注入引擎 ──────────────────────────────────────────────
engine = AgentEngine(presets=presets, middleware=middleware)
```

适合放在这里的能力包括配额、审批、审计、追踪和重试。认证、鉴权和租户解析仍应由宿主业务系统完成，再通过 `AgentContext.user` 或 `AgentContext.extras` 传入。

## RuntimeEvent 契约

`RuntimeEvent` 是引擎原生事件。业务系统可以把它转成 SSE、WebSocket、gRPC stream，或者收集后同步返回。

常见事件：

| 事件 | 说明 |
|------|------|
| `RunStarted` / `RunCompleted` / `RunFailed` / `RunCancelled` | 运行生命周期 |
| `TurnStarted` / `TurnEnded` | 轮次开始/结束 |
| `TextDelta` / `ReasoningDelta` | 文本/推理增量（流式输出） |
| `ToolCallStarted` / `ToolCallCompleted` / `ToolCallFailed` | 工具调用生命周期 |
| `ToolStreamEventEmitted` | 工具流式事件 |
| `UsageReport` | Token 用量统计 |
| `ApprovalRequired` | 需要人工审批（破坏性工具） |
| `TodosUpdated` | 任务清单更新 |
| `UserQuestionAsked` | Agent 向用户提问 |

订阅方式：

```python
# 定义事件处理函数
async def on_event(event):
    # 转成字典后发送到 WebSocket
    await websocket.send_json(event.to_dict())

# 执行 Agent 并订阅事件
await engine.run(
    agent_name="support",
    query=query,
    context=context,
    on_event=on_event,  # 传入回调函数
)
```

SSE 只是 reference app 的一种适配，位于 `examples/reference_app/services/web_api.py`。

## Reference App

本仓库保留一个参考实现：

| 路径 | 说明 |
|------|------|
| `examples/reference_app/agents/` | 示例 Agent preset |
| `examples/reference_app/services/agent_orchestration_service.py` | 兼容旧脚本的应用层包装 |
| `examples/reference_app/services/web_api.py` | FastAPI/SSE 示例，不是核心引擎 |

启动参考 Web API：

```bash
# 启动后端（FastAPI + SSE）
uv run --env-file .env uvicorn examples.reference_app.services.web_api:app --host 127.0.0.1 --port 8000
```
