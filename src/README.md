# Agent Core 源码导览

此目录包含独立的 Agent 框架代码。业务 Agent 位于 `src/agents` 下；应用入口点位于 `src/services` 下。

## 目录结构

```text
agent_core/
  base/          BaseAgent, AgentContext, AgentState
  handlers/      ReActHandler, PipelineHandler, LegacyHandler
  llm/           OpenAI 兼容客户端和 LLM 协议类型
  memory/        Message 和 Memory
  runtime/       TurnRunner, RunState, 语义 RuntimeEvent 类
  stream/        EventStream, EventType, Printer SSE 信封
  tools/         Tool, StreamingTool, ToolExecutor, 注册表, 集合
  tools/builtin/ ReadFileTool, SkillTool
  persistence/   PersistencePort 协议
  observability/ RunEventLog JSONL 写入器
```

## 规划模型

深度研究使用模型原生规划。没有单独的有状态规划工具。
模型被提示分解工作，然后 ReAct 循环执行模型请求的任何工具调用，并将工具结果反馈到记忆中。

这保持规划在模型轮次中，执行在事件流中：

```text
用户请求
  -> step
  -> thinking
  -> tool_call_start
  -> tool_result
  -> step_end
  -> 下一步或最终结果
```

## 公共流式协议

`Printer` 发出传统的 SSE 信封格式：

```json
{
  "responseType": "text",
  "response": "...",
  "responseAll": "",
  "useTimes": 0,
  "reqId": "req-1",
  "errorMsg": null,
  "resultMap": null,
  "conversation_id": "conv-1",
  "finished": false
}
```

当前事件类型：

- 生命周期：`start`、`step`、`step_end`、`usage`
- 模型输出：`thinking`、`text`
- 工具生命周期：`tool_call_start`、`tool_result`
- 兼容性：`task`、`tool_thought`、`search_result`、`final_result`
- 终止：`result`、`error`、`done`

Web 和终端渲染器应将其视为追踪协议，而不是原始日志。按步骤分组事件，将工具调用渲染为卡片，并将完成的卡片冻结在原位。

## 运行时事件

运行时事件是由 `TurnRunner` 和 `ToolExecutor` 记录的内部诊断信息。它们与外部 SSE 协议分开：

- `RunStarted`、`RunCompleted`、`RunFailed`、`RunCancelled`
- `TurnStarted`、`TurnEnded`、`UsageReport`
- `ReasoningDelta`、`TextDelta`
- `ToolCallStarted`、`ToolCallCompleted`、`ToolCallFailed`

`Printer.from_runtime_event()` 可以在需要时将语义事件桥接到公共流，但两层应保持分离。

## 工具

单次调用使用 `Tool`，当工具本身可以发出中间事件时使用 `StreamingTool`。内置工具：

- `ReadFileTool`：用于演示和本地工作流的安全只读文件预览。
- `SkillTool`：加载本地技能，以便 Agent 可以调用专用流程。

工具执行集中在 `ToolExecutor` 中，它处理超时、破坏性工具警告、结果截断和运行时工具事件。

## 协议变更时需要更新的测试

- `tests/test_streaming_protocol.py`
- `tests/test_sse_golden_compatibility.py`
- `tests/test_printer_runtime_event_mapping.py`
- `tests/test_react_handler.py`

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
