# AgentEngine 文档

## 集成入口

| 文档 | 说明 |
|---|---|
| [../INTEGRATION.md](../INTEGRATION.md) | 业务系统如何嵌入 AgentEngine |
| [PUBLIC_API.md](PUBLIC_API.md) | 公共 API、稳定性和内部边界 |
| [API.md](API.md) | 核心对象和事件契约速览 |

## 核心模块

| 文档 | 模块 |
|---|---|
| [agentengine/base.md](agentengine/base.md) | AgentRun / AgentContext / AgentState |
| [agentengine/runtime.md](agentengine/runtime.md) | TurnRunner / run_turn / RuntimeEvent |
| [agentengine/llm.md](agentengine/llm.md) | LLMClient / OpenAI-compatible client |
| [agentengine/memory.md](agentengine/memory.md) | Message / Memory |
| [agentengine/tools.md](agentengine/tools.md) | Tool / ToolCollection / ToolExecutor |
| [agentengine/stream.md](agentengine/stream.md) | SSE adapter primitives |
| [agentengine/persistence.md](agentengine/persistence.md) | PersistencePort |
| [agentengine/concurrency.md](agentengine/concurrency.md) | ConversationLockManager |
| [agentengine/enterprise.md](agentengine/enterprise.md) | Optional middleware |
| [agentengine/hooks.md](agentengine/hooks.md) | HookManager |

## 参考实现

`examples/reference_app/` 是本仓库自带的参考应用，包含示例 agents、应用层 service 和 FastAPI/SSE endpoint。它用于演示和测试，不属于核心 SDK 发布物。

启动参考后端：

```bash
uv run --env-file .env uvicorn examples.reference_app.services.web_api:app --host 127.0.0.1 --port 8000
```
