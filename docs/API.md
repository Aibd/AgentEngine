# AgentEngine API

本文是核心契约速览。更完整的集成说明见 [../INTEGRATION.md](../INTEGRATION.md)，稳定性边界见 [PUBLIC_API.md](PUBLIC_API.md)。

## AgentEngine

`AgentEngine` 是业务系统嵌入 AgentEngine 的正式 SDK 入口：

```python
from agentengine import AgentContext, AgentEngine, AgentPreset

engine = AgentEngine(
    presets={"chat": AgentPreset(name="chat", instructions="Answer briefly.")}
)

context = AgentContext(request_id="req-1", query="hello", llm=llm)
result = await engine.run(agent_name="chat", query="hello", context=context)
```

核心 SDK 不会自动读取环境变量，也不会自动注册业务 Agent。调用方需要显式传入 `presets`、`config_resolver`、`context.llm` 或 `llm_factory`。

## AgentPreset / RunConfig

`AgentPreset` 是业务友好的 Agent 声明，最终会编译成 `RunConfig`：

```python
AgentPreset(
    name="support",
    instructions="You are a concise support assistant.",
    max_turns=4,
)
```

`RunConfig` 是运行时真正使用的不可变配置，包含初始消息、轮数上限、setup/teardown hooks 和 extras。

## AgentContext

`AgentContext` 是宿主业务系统传入引擎的上下文：

- `request_id`
- `query`
- `llm`
- `tool_collection`
- `session_id`
- `conversation_id`
- `user`
- `db`
- `persistence`
- `extras`

`AgentContext` 不会自动注册 `SkillTool`。需要 Skill 能力时，由 preset setup hook 或调用方显式加入 `tool_collection`。

## LLMClient

```python
async def chat(
    messages,
    *,
    tools=None,
    stream=False,
    **kwargs,
) -> LLMResponse: ...

async def chat_stream(
    messages,
    *,
    tools=None,
    **kwargs,
) -> AsyncIterator[LLMChunk]: ...
```

`LLMResponse` 和 `LLMChunk` 支持 `content`、`reasoning_content`、`tool_calls`、`finish_reason`、`usage` 和 `raw`。

## Tool

工具通过 `ToolCollection` 显式传入：

```python
from agentengine import Tool, ToolCollection

class SearchTool(Tool):
    name = "search"
    description = "Search internal docs."
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}

    async def run(self, **kwargs):
        return "result"

context.tool_collection = ToolCollection([SearchTool()])
```

## PersistencePort

业务系统可以实现 `PersistencePort` 接自己的 MySQL、Postgres、Redis 或领域存储。只要 `conversation_id` 非空，引擎会在 run 前加载消息，结束后保存消息。

## ConversationLockManager

默认 `InMemoryConversationLockManager` 只适合单进程。多副本部署应注入 `RedisConversationLockManager` 或业务自己的分布式锁实现。

## RuntimeEvent

`RuntimeEvent` 是引擎原生事件，SSE 只是参考适配。业务系统可以用 `on_event` 订阅：

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

稳定事件包括 `RunStarted`、`TextDelta`、`ToolCallStarted`、`ToolCallCompleted`、`UsageReport`、`RunCompleted`、`RunFailed` 等。

## Reference App

`examples/reference_app/services/agent_orchestration_service.py` 是参考应用兼容包装，不是核心 SDK。它保留了示例 preset registry 和 env LLM factory，方便本仓库 CLI/Web 测试使用。
