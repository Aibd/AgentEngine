# Public API

本文档划分 SDK 稳定边界。`agentengine` 包内的公开对象遵循语义化版本兼容；内部模块、reference app 和测试辅助代码不承诺兼容。

## 版本策略

- **v0.x（当前）**：公共 API 仍在收敛，minor 版本（0.1 → 0.2）允许破坏性改动，但会在 CHANGELOG 中显式标注。patch 版本（0.1.0 → 0.1.1）只做向后兼容的修复。
- **v1.0 之后**：遵循 [SemVer](https://semver.org/lang/zh-CN/)。本文档「稳定入口」与「事件契约」两节列出的对象，破坏性改动只能在 major 版本（1.x → 2.x）中发生。
- **非稳定区域**（见文末列表）：任何版本都可能调整，业务系统不应直接依赖。

升级建议：业务系统在 `pyproject.toml` 中固定到 minor 版本（`agentengine~=0.1`），升 minor 前阅读 CHANGELOG。

## 稳定入口

优先从包根导入：

```python
from agentengine import (
    AgentContext,
    AgentEngine,
    AgentPreset,
    RunConfig,
    Tool,
    ToolCollection,
    LLMClient,
    PersistencePort,
    ConversationLockManager,
    RuntimeEvent,
)
```

这些对象属于公共契约：

- `AgentEngine`
- `AgentContext`
- `AgentPreset`
- `RunConfig`
- `Tool`, `StreamingTool`, `ToolStreamEvent`, `ToolCollection`
- `LLMClient`, `LLMResponse`, `LLMChunk`
- `PersistencePort`
- `ConversationLockManager`, `InMemoryConversationLockManager`, `RedisConversationLockManager`
- `RuntimeEvent` 及其事件子类
- `MiddlewareChain` 和 `agentengine.enterprise` 中的显式中间件工厂

## 事件契约

`RuntimeEvent.to_dict()` 是跨传输层的稳定序列化形状。业务系统可以直接使用这些事件构建 SSE、WebSocket、队列消息或审计日志。

稳定事件类型包括：

- `run_started`
- `turn_started`
- `text_delta`
- `reasoning_delta`
- `tool_call_started`
- `tool_stream_event`
- `tool_call_completed`
- `tool_call_failed`
- `turn_ended`
- `usage_report`
- `run_completed`
- `run_failed`
- `run_cancelled`
- `approval_required`
- `todos_updated`
- `user_question_asked`

## 非稳定区域

以下内容是内部实现或参考实现，可能在 minor 版本中调整：

- `agentengine.runtime.turn` 内部函数
- `agentengine.runtime.turn_runner.TurnRunner` 的内部编排细节
- `agentengine.tools.executor` 的内部结果裁剪策略
- `examples/reference_app/*`
- `scripts/*`
- `web/*`

如果业务系统需要依赖这些内部细节，优先提出公共接口需求，而不是直接绑定内部模块。
