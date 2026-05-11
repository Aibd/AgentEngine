# AgentEngine 集成指南

AgentEngine 的定位是被业务系统嵌入的 Agent 运行引擎。业务系统负责认证、鉴权、租户路由、HTTP/WebSocket/gRPC 端点和配置中心；AgentEngine 负责 LLM 调用、ReAct 循环、工具调度、事件流、持久化端口和会话级锁。

## 最小集成

```python
from agentengine import AgentContext, AgentEngine, AgentPreset
from your_app.llm import llm_client

engine = AgentEngine(
    presets={
        "support": AgentPreset(
            name="support",
            instructions="You are a concise support assistant.",
            max_turns=4,
        )
    }
)

context = AgentContext(
    request_id="req-123",
    query="帮我查一下订单状态",
    llm=llm_client,
    user=current_user,
    conversation_id="conv-456",
)

answer = await engine.run(
    agent_name="support",
    query=context.query,
    context=context,
)
```

## 注入 LLM

核心 SDK 不会读取环境变量。业务系统可以直接把 LLM 放进 `AgentContext`，也可以给 `AgentEngine` 一个工厂：

```python
engine = AgentEngine(
    presets={"support": AgentPreset(name="support")},
    llm_factory=lambda: app_container.llm_client(),
)
```

`LLMClient` 需要实现：

```python
async def chat(messages, *, tools=None, stream=False, **kwargs): ...
async def chat_stream(messages, *, tools=None, **kwargs): ...
```

项目自带 `agentengine.llm.OpenAICompatibleClient` 和 `create_llm_from_env()`，但环境变量工厂只适合脚本或 reference app，不是核心 SDK 默认行为。

## 注入持久化

业务系统实现 `PersistencePort` 后注入：

```python
engine = AgentEngine(
    presets=presets,
    persistence=PostgresPersistence(pool),
)

context = AgentContext(
    request_id=request_id,
    query=query,
    llm=llm,
    conversation_id=conversation_id,
)
```

当 `conversation_id` 非空时，引擎会在 run 前加载历史消息，结束后保存消息。

## 注入工具

工具通过 `AgentContext.tool_collection` 显式注入：

```python
from agentengine import AgentContext, Tool, ToolCollection

class OrderLookupTool(Tool):
    name = "lookup_order"
    description = "Look up an order by id."
    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    }

    async def run(self, **kwargs):
        return await order_service.lookup(kwargs["order_id"])

context = AgentContext(
    request_id=request_id,
    query=query,
    llm=llm,
    tool_collection=ToolCollection([OrderLookupTool()]),
)
```

`SkillTool` 也需要显式注册。`AgentContext` 不会自动给所有 Agent 加 Skill 能力。

## 会话锁

单进程可以使用默认 `InMemoryConversationLockManager`。多副本部署必须注入分布式锁：

```python
from agentengine import RedisConversationLockManager

engine = AgentEngine(
    presets=presets,
    lock_manager=RedisConversationLockManager(redis_client),
)
```

Redis 版本是参考实现，使用 `SET NX PX` 获取锁，并用 compare-and-delete 脚本释放锁。生产环境需要根据业务运行时长配置合理 TTL。

## Middleware

`agentengine.enterprise` 是可选中间件集合，默认关闭。业务系统按需启用：

```python
from agentengine.enterprise import MiddlewareChain, QuotaStore, quota_middleware

quota_store = QuotaStore()
middleware = MiddlewareChain([quota_middleware(quota_store)])

engine = AgentEngine(presets=presets, middleware=middleware)
```

适合放在这里的能力包括配额、审批、审计、追踪和重试。认证、鉴权和租户解析仍应由宿主业务系统完成，再通过 `AgentContext.user` 或 `AgentContext.extras` 传入。

## RuntimeEvent 契约

`RuntimeEvent` 是引擎原生事件。业务系统可以把它转成 SSE、WebSocket、gRPC stream，或者收集后同步返回。

常见事件：

- `RunStarted` / `RunCompleted` / `RunFailed` / `RunCancelled`
- `TurnStarted` / `TurnEnded`
- `TextDelta` / `ReasoningDelta`
- `ToolCallStarted` / `ToolCallCompleted` / `ToolCallFailed`
- `ToolStreamEventEmitted`
- `UsageReport`
- `ApprovalRequired`
- `TodosUpdated`
- `UserQuestionAsked`

订阅方式：

```python
async def on_event(event):
    await websocket.send_json(event.to_dict())

await engine.run(
    agent_name="support",
    query=query,
    context=context,
    on_event=on_event,
)
```

SSE 只是 reference app 的一种适配，位于 `examples/reference_app/services/web_api.py`。

## Reference App

本仓库保留一个参考实现：

- `examples/reference_app/agents/`: 示例 Agent preset
- `examples/reference_app/services/agent_orchestration_service.py`: 兼容旧脚本的应用层包装
- `examples/reference_app/services/web_api.py`: FastAPI/SSE 示例，不是核心引擎

启动参考 Web API：

```bash
uv run --env-file .env uvicorn examples.reference_app.services.web_api:app --host 127.0.0.1 --port 8000
```
