# Agent Core API

本文档记录 `agent_core` 当前对应用层开放的核心契约。未在这里列出的模块视为内部实现细节，发布前不建议外部直接依赖。

## Agent 生命周期

`BaseAgent` 是一次 agent run 的上下文容器，loop 逻辑由 handler 执行。

可覆写钩子：

- `setup() -> None`：运行前调用一次，用于注册工具、写入初始 memory、注入 `context.extras`。
- `teardown() -> None`：运行结束后调用一次，成功、异常、取消路径都会触发，用于释放本次运行创建的资源。
- `system_prompt() -> str`：返回系统提示词，空字符串表示不注入。
- `next_step_prompt() -> str`：返回每轮用户消息前的额外指导。
- `pipeline_steps() -> list[PipelineStep]`：为 `PipelineHandler` 提供固定工作流步骤。

Handler 会维护 `agent.state`：

- `IDLE`
- `RUNNING`
- `FINISHED`
- `ERROR`
- `CANCELLED`

## AgentContext

`AgentContext` 是 handler、agent、工具和服务之间共享的运行上下文。

常用字段：

- `request_id: str`
- `query: str`
- `llm: LLMClient | None`
- `printer: Printer | None`
- `tool_collection: ToolCollection`
- `session_id: str`
- `conversation_id: str`
- `extras: dict[str, Any]`

`AgentContext` 初始化时会自动注册 `SkillTool`，使每个 agent 默认具备技能入口。

## LLMClient

`LLMClient` 是协议类型，生产实现目前只正式支持 `OpenAICompatibleClient`。

必需方法：

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

`LLMResponse` 字段：

- `content`
- `reasoning_content`
- `tool_calls`
- `finish_reason`
- `usage`
- `raw`

`LLMChunk` 字段：

- `content`
- `reasoning_content`
- `finish_reason`
- `usage`
- `raw`

`create_llm_from_env()` 从环境变量创建 OpenAI-compatible 客户端：

- `LLM_API_KEY`
- `LLM_MODEL`
- `LLM_BASE_URL`
- `LLM_TIMEOUT`
- `LLM_MAX_RETRIES`

## 错误模型

所有可匹配的框架错误继承 `AgentCoreError`，并提供 `to_dict()`：

```python
{
    "code": "llm_timeout",
    "message": "provider timed out",
    "category": "llm",
    "retryable": True,
    "status_code": 429,
    "details": {},
}
```

当前核心错误类型：

- `LLMError`
- `LLMHTTPError`
- `LLMRateLimitError`
- `LLMTimeoutError`
- `LLMConnectionError`
- `LLMStreamError`
- `ToolExecutionError`

`Printer.error()` 和 handler 错误事件会保留兼容字段 `errorMsg`，同时在 `response` 内输出结构化错误。

## 流式事件

`Printer` 输出兼容现有 SSE 的 envelope：

- `responseType`
- `response`
- `responseAll`
- `useTimes`
- `reqId`
- `errorMsg`
- `resultMap`
- `conversation_id`
- `finished`

终态事件：

- `error`
- `done`
- `final_result`

## Registry

正式 registry：

- `register_agent()` / `create_agent()` / `registered_agents()`
- `register_handler()` / `create_handler()` / `registered_handlers()`
- `register_tool()` / `create_tool()` / `registered_tools()`

Registry 使用模块级 `RLock` 保护读写。`registered_*()` 返回副本，调用方修改副本不会影响全局 registry。

## Memory

`Memory` 是有序消息存储，支持可选上限 `max_messages`。

常用方法：

- `append()`
- `extend()`
- `clear()`
- `snapshot()`
- `to_openai()`
- `add_system_message()`
- `add_user_message()`
- `add_assistant_message()`
- `add_tool_message()`
- `last_user_message()`
- `last_assistant_message()`

`Memory` 使用实例级锁保护内部列表。为了并发读取稳定性，外部代码应优先使用 `snapshot()`，不要长期持有 `messages` 的直接引用。

## OrchestrationService

`AgentOrchestrationService` 是应用层入口：

```python
service = AgentOrchestrationService(config_path="config/agents.yaml")
result = await service.run(agent_name="general_chat", query="hello")
await service.close()
```

职责：

- 校验输入
- 读取 agent 配置
- 创建或复用 `AgentContext`
- 注入 LLM
- 创建 agent 和 handler
- 收集运行诊断到 `context.extras`
- 关闭托管 LLM 资源

流式入口：

```python
context, event_stream = service.create_streaming_context(
    request_id="req-1",
    query="hello",
    conversation_id="conv-1",
)
```
