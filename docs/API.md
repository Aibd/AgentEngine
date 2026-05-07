# Agent Core API

> ⚠️ **此文档正在重构中。** `BaseAgent` 已被 `AgentSpec + AgentRun` 取代（Phase 2 完成）。
> `pipeline_steps()` 方法已随 `PipelineHandler` 一起删除（Phase 1 完成）。
> 请参阅 [REFACTOR_PLAN.md](REFACTOR_PLAN.md) 了解完整路线图。

本文档描述了 Agent、Handler、工具、LLM 客户端、运行时事件和公共流式信封的稳定契约。

## AgentSpec / AgentRun（取代 BaseAgent）

`BaseAgent` 是一个运行容器。循环逻辑位于 Handler 中。

- `setup() -> None`：注册工具、初始化记忆或设置上下文额外数据。
- `teardown() -> None`：释放每次运行的资源。
- `system_prompt() -> str`：返回系统提示词。
- `next_step_prompt() -> str`：可选的指导，在每个 ReAct 轮次中插入到最后一条用户消息之前。
- `pipeline_steps() -> list[PipelineStep]`：`PipelineHandler` 的固定工作流步骤。

Handler 拥有的状态值包括 `IDLE`、`RUNNING`、`FINISHED`、`ERROR` 和 `CANCELLED`。

## AgentContext

`AgentContext` 携带一次运行的依赖项：

- `request_id: str`
- `query: str`
- `llm: LLMClient | None`
- `printer: Printer | None`
- `tool_collection: ToolCollection`
- `session_id: str`
- `conversation_id: str`
- `user: Any`
- `db: Any`
- `persistence: PersistencePort | None`
- `extras: dict[str, Any]`

除非已存在名为 `Skill` 的工具，否则 `AgentContext` 会自动注册 `SkillTool`。

## LLMClient

```python
async def chat(
    messages: list[Message],
    *,
    tools: list[dict[str, Any]] | None = None,
    stream: bool = False,
    **kwargs: Any,
) -> LLMResponse: ...

async def chat_stream(
    messages: list[Message],
    *,
    tools: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> AsyncIterator[LLMChunk]: ...
```

`LLMResponse` 字段：`content`、`reasoning_content`、`tool_calls`、`finish_reason`、`usage`、`raw`。

`LLMChunk` 字段：`content`、`reasoning_content`、`finish_reason`、`usage`、`raw`。

## 记忆消息

`Message.tool_calls` 存储原始 OpenAI 兼容的工具调用字典。因此助手消息可以写入记忆，并在后续工具轮次中原样发回给 LLM。

`Message.reasoning_content` 保留提供商特定的推理增量，用于后续调用和公共 `thinking` 事件。

## 公共流式信封

`Printer` 发出终端和 Web 渲染器消费的稳定 SSE 信封：

```json
{
  "responseType": "tool_result",
  "response": "...",
  "responseAll": "",
  "useTimes": 0,
  "reqId": "req-1",
  "errorMsg": null,
  "resultMap": {},
  "conversation_id": "conv-1",
  "finished": false
}
```

当前的 `responseType` 值：

- 生命周期：`start`、`step`、`step_end`、`usage`
- 模型输出：`thinking`、`text`
- 工具生命周期：`tool_call_start`、`tool_result`
- 兼容性：`task`、`tool_thought`、`search_result`、`final_result`
- 终止：`result`、`error`、`done`

终止事件设置 `finished=True`：`result`、`error`、`done` 和 `final_result`。

## 运行时事件

运行时事件是语义诊断信息，不是公共协议。它们位于 `agent_core.runtime.events` 中，可通过 `to_dict()` 序列化。

- 运行生命周期：`RunStarted`、`RunCompleted`、`RunFailed`、`RunCancelled`
- 轮次生命周期：`TurnStarted`、`TurnEnded`、`UsageReport`
- 模型增量：`ReasoningDelta`、`TextDelta`
- 工具生命周期：`ToolCallStarted`、`ToolCallCompleted`、`ToolCallFailed`

`TurnRunner` 记录运行生命周期事件。`ToolExecutor` 记录工具事件。`Printer.from_runtime_event()` 可以在需要时将事件桥接到公共 SSE 信封。

## 工具

`Tool` 是单次调用的基础接口。`StreamingTool` 可以在运行时发出中间 `ToolStreamEvent` 值。

工具执行集中在 `ToolExecutor` 中，它处理：

- 超时强制执行
- 破坏性工具警告
- 结果截断
- 运行时 `ToolCallStarted`、`ToolCallCompleted` 和 `ToolCallFailed` 事件
- 将流式工具事件转发到 `Printer`

内置工具：

- `ReadFileTool`
- `SkillTool`

## 注册表

- `register_agent()` / `create_agent()` / `registered_agents()`
- `register_handler()` / `create_handler()` / `registered_handlers()`
- `register_tool()` / `create_tool()` / `registered_tools()`

注册表的读写由 `RLock` 保护；`registered_*()` 返回副本。

## OrchestrationService

`AgentOrchestrationService` 验证输入，在需要时创建 `AgentContext`，解析配置的 Agent 和 Handler，通过 `TurnRunner` 运行，并在 `context.extras` 中记录有用的元数据。

```python
service = AgentOrchestrationService(config_path="config/agents.yaml")
context, stream = service.create_streaming_context(
    request_id="req-1",
    query="hello",
    conversation_id="conv-1",
)
result = await service.run(
    agent_name="general_chat",
    query="hello",
    context=context,
)
```
