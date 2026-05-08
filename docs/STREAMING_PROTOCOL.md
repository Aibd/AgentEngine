# Streaming Protocol v2（流式协议 v2）

Agent Core 将运行时输出暴露为标准的 Server-Sent Events（SSE）。运行时代码会发出语义化的 `RuntimeEvent` 对象；`SseSink` 将这些对象渲染为 SSE v2 帧。

## 传输格式

每一帧使用命名的 SSE 事件：

```text
event: text
data: {"delta": "hello", "run_id": "run_...", "turn_id": "turn_...", "request_id": "web-...", "conversation_id": "web-conversation"}

event: tool_result
data: {"tool": "read_file", "ok": true, "result": "...", "elapsed_ms": 42, "tool_call_id": "call_1"}

event: done
data: {"reason": "completed", "result": "...", "elapsed_ms": 1234}
```

流中也可能包含元数据注释：

```text
: run_id=run_abc turn_id=turn_def request_id=web-123 conversation_id=web-conversation
```

HTTP 响应头包含：

- `X-Streaming-Protocol: agent-core.sse.v2`
- `X-Request-ID: <request-id>`
- `X-Conversation-ID: <conversation-id>`

## 事件顺序

典型的 ReAct 运行流程：

```text
start
step
thinking*
text*
tool_call_start?
tool_result?
todos_updated?
user_question_asked?
step_end
...
usage
done
```

`*` 表示可出现零次或多次，`?` 表示可出现零次或一次。`todos_updated` 和 `user_question_asked` 由对应的内置工具发出（`TodoWriteTool` / `AskUserQuestionTool`），未调用时整个 run 都不会出现。

失败时以 `error` 代替 `done` 结束。

## 事件数据

### `start`（开始）

```json
{
  "query": "用户输入",
  "agent": "general_chat",
  "run_id": "run_...",
  "turn_id": "turn_...",
  "request_id": "web-...",
  "conversation_id": "web-conversation"
}
```

### `step`（步骤）

```json
{ "turn": 1 }
```

### `thinking`（思考）

推理 / 模型思考片段（delta）。

```json
{ "delta": "..." }
```

### `text`（文本）

助手回答片段（delta）。

```json
{ "delta": "..." }
```

### `tool_call_start`（工具调用开始）

```json
{
  "tool": "read_file",
  "arguments": { "path": "README.md" },
  "tool_call_id": "call_1"
}
```

### `tool_result`（工具结果）

```json
{
  "tool": "read_file",
  "ok": true,
  "result": "文件内容或摘要",
  "elapsed_ms": 42,
  "tool_call_id": "call_1"
}
```

调用失败的工具会设置 `ok: false`，并可能包含 `error_type` 和 `error_message`。

### `step_end`（步骤结束）

```json
{
  "turn": 1,
  "has_tool_calls": true,
  "elapsed_ms": 500
}
```

### `usage`（用量）

```json
{
  "prompt_tokens": 100,
  "completion_tokens": 50,
  "total_tokens": 150,
  "total_seconds": 2.5,
  "elapsed_ms": 2500
}
```

### `done`（完成）

```json
{
  "reason": "completed",
  "result": "最终回答摘要",
  "elapsed_ms": 1234
}
```

### `error`（错误）

当可用时，payload 为 `AgentCoreError.to_dict()`：

```json
{
  "code": "llm_timeout",
  "message": "provider timed out",
  "category": "llm",
  "retryable": true,
  "details": {}
}
```

### `todos_updated`（任务清单变更）

`TodoWriteTool` 重写当次会话任务清单时发出。携带完整列表（不是 diff）。每条 todo 三个字段：

```json
{
  "todos": [
    { "content": "Build", "activeForm": "Building", "status": "in_progress" },
    { "content": "Ship",  "activeForm": "Shipping", "status": "pending" }
  ]
}
```

`status` 取值 `pending` | `in_progress` | `completed`。任意时刻最多一个 `in_progress`。当全部 `completed` 时，工具会清空存储，但本事件携带的列表仍是完成态——前端按需展示完成动画或直接折叠。

### `user_question_asked`（agent 反向追问）

`AskUserQuestionTool` 在 agent 需要消歧时发出。工具不会阻塞 run，而是立即返回占位结果；用户答复在下一轮以普通 user message 流入。

```json
{
  "question_id": "q_abc",
  "question": "Pick a backend",
  "options": ["pg", "mysql"],
  "multiple": false
}
```

`options` 可为空（自由作答）；`multiple=true` 表示多选。

## 兼容性说明

v1 使用一个无名称的 SSE 消息，其中包含一个大的 JSON 信封：

```json
{
  "responseType": "text",
  "response": "...",
  "responseAll": "",
  "useTimes": 0,
  "resultMap": null
}
```

v2 将信封从实时协议中移除。消费者应依据 SSE 的 `event:` 名称进行分发，并读取紧凑的 `data` 对象。术语 `responseType`、`responseAll`、`useTimes`、`resultMap` 和 `errorMsg` 不再由 `Printer`、`SseSink`、Web 端点或 CLI 渲染器发出。

事件名称 `task`、`tool_thought`、`search_result` 和 `final_result` 仍为流式工具保留。它们仍以 v2 命名事件的形式交付，并附带紧凑的 `data` payload。
