# AgentEngine 集成说明

本文档面向需要把 AgentEngine 接入业务系统的开发者

## 集成目标

AgentEngine 作为 Agent 运行底座，负责 Agent 的运行循环、LLM 调用、工具执行、事件分发、持久化接入、并发控制和企业中间件能力。业务系统主要负责用户鉴权、租户路由、模型配置、业务工具实现和前端/接口适配。

## 推荐接入边界

业务系统建议只依赖包根公开 API：

```python
from agentengine import AgentContext, AgentEngine, AgentPreset, Tool
```

需要完整公开对象和签名时，参考根目录 [PUBLIC_API.md](../PUBLIC_API.md)。

## 最小接入示例

```python
from agentengine import AgentContext, AgentEngine, AgentPreset
from agentengine.llm.openai_compat import OpenAICompatibleClient

llm = OpenAICompatibleClient(
    base_url="https://api.deepseek.com/v1",
    api_key="your-api-key",
    model="deepseek-chat",
)

engine = AgentEngine(
    presets={
        "assistant": AgentPreset(
            name="assistant",
            instructions="你是业务助手，请基于用户问题给出准确回答。",
        )
    }
)

context = AgentContext(
    request_id="req-001",
    query="你好，帮我介绍一下当前能力",
    conversation_id="conv-001",
    llm=llm,
)

answer = await engine.run(
    agent_name="assistant",
    query=context.query,
    context=context,
)
```

## 流式接入方式

Web 服务推荐使用 `create_streaming_context()`，再把 `event_stream` 转成 SSE 或 WebSocket。

```python
context, event_stream = engine.create_streaming_context(
    request_id="req-001",
    query="帮我分析这份数据",
    conversation_id="conv-001",
)
context.llm = llm

task = asyncio.create_task(
    engine.run(agent_name="assistant", query=context.query, context=context)
)

async for frame in event_stream:
    # frame 形如 {"event": "text", "data": {...}}
    yield to_sse(frame)

answer = await task
```

事件名包括：

- `start`
- `step`
- `thinking`
- `text`
- `tool_call_start`
- `tool_result`
- `step_end`
- `usage`
- `done`
- `error`

## 自定义工具接入

业务工具继承 `Tool` 并实现 `run()`：

```python
from typing import Any
from agentengine import Tool

class UserProfileTool(Tool):
    name = "lookup_user_profile"
    description = "按用户 ID 查询用户资料"
    schema = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "用户 ID"}
        },
        "required": ["user_id"],
    }

    async def run(self, **kwargs: Any) -> str:
        user_id = str(kwargs.get("user_id", ""))
        return f"用户 {user_id} 的资料"
```

注册方式建议放在 `AgentPreset.setup`：

```python
from agentengine import AgentContext, AgentPreset

async def setup_tools(context: AgentContext) -> None:
    context.tool_collection.add(UserProfileTool())

preset = AgentPreset(
    name="profile_agent",
    instructions="需要用户资料时调用 lookup_user_profile。",
    setup=setup_tools,
)
```

## 生产建议

- 统一生成并透传 `request_id`，便于日志、追踪和问题定位。
- 按业务会话传入 `conversation_id`，需要恢复上下文时接入持久化。
- 多实例部署使用 `RedisConversationLockManager`，不要只依赖内存锁。
- 高风险工具接入 `ExecPolicy` 或 `approval_middleware`。
- 按租户接入 `TenantContext` 和 `quota_middleware`。
- 对前端稳定暴露 SSE v2 事件，不直接暴露内部 RuntimeEvent 类。
- 业务代码不要 import `examples.*`，示例目录只用于参考。

## 相关文档

- [0.2 版本说明](./release-0.2.md)
- [公开 API](../PUBLIC_API.md)
- [项目 README](../README.md)

