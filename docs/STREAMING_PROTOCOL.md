# 流式协议

> ⚠️ **此文档正在重构中。** Phase 5 将删除 `responseAll`、`useTimes` 兼容字段，并将
> `responseType` 从 JSON 正文移至 SSE 原生 `event:` 行。届时将同步更新本文档。
> 请参阅 [REFACTOR_PLAN.md](REFACTOR_PLAN.md) 了解完整路线图。

Agent Core 将事件作为单个 SSE 流发出，由终端渲染器（`scripts/chat_pretty.py`）和 React UI（`web/`）共同消费。本文档是服务器与渲染器之间的**传输层契约**。

> 添加新的事件类型是安全的。删除或重新利用现有类型是破坏性变更——如果这样做，请提升契约版本。

---

## 信封

每个事件都是推送到 SSE 通道的 JSON 对象，格式为 `data: <json>\n\n`：

```json
{
  "responseType":   "<event-type>",
  "response":       <primary-payload>,
  "responseAll":    "",
  "useTimes":       0,
  "reqId":          "<request-id>",
  "errorMsg":       "<message-or-null>",
  "resultMap":      <structured-payload-or-null>,
  "conversation_id":"<conversation-id>",
  "finished":       <boolean>
}
```

字段语义：

| 字段 | 作用 |
|------------------|-----------------------------------------------------------------------------------------------|
| `responseType`   | 下面列出的事件类型之一。**稳定的。**                                              |
| `response`       | 简短的人类可读文本（遗留）。对于大多数类型，渲染器改为读取 `resultMap`。    |
| `responseAll`    | 为遗留前端保留。在新协议中始终为空。                              |
| `useTimes`       | 保留字段。始终为 `0`。                                                                         |
| `reqId`          | 调用方提供的请求 ID。一次运行中保持不变。                                             |
| `errorMsg`       | 仅在 `error` 时非空。为遗留客户端镜像 `resultMap.message`。                     |
| `resultMap`      | 结构化负载——**新客户端的事实来源**。                                 |
| `conversation_id`| 调用方提供的会话 ID。一次运行中保持不变。                                        |
| `finished`       | 仅在运行的终止事件上为 `true`（`result`、`error`、`done`）。                       |

---

## 事件时间线

每轮 ReAct：

```
start ──▶ step ──▶ [thinking ...] ──▶ [text ...] ──▶ [tool_call_start ──▶ tool_result] ...
                                                                                        │
                                                                                        ▼
                                                                                     step_end
                                                                                        │
                                              (循环：另一步，或中断完成)  │
                                                                                        ▼
                                                                              ... usage ──▶ result
                                                                                        │
                                                                                  (或 error)
```

流式保证：
- `start` 始终排在第一位；`result` / `error` 之一始终排在最后。
- `step` 和 `step_end` 成对出现，每轮 ReAct 迭代一对。
- `text` 和 `thinking` 增量只出现在同一轮次的 `step` 和 `step_end` 之间。
- 同一调用的 `tool_call_start` 和 `tool_result` 在 `resultMap` 中共享 `tool_call_id`。
- `usage` 只发出一次，在 `result` 之前。

---

## 事件参考

### `start`
运行开始。由 `Printer.start(query)` 发出。

```json
"resultMap": null,
"response":  "开始处理: <query>"
```

渲染器应使用 `response`（去掉 `开始处理: ` 前缀）或维护自己的用户请求副本。

---

### `step`
新的 ReAct 迭代开始。即将调用模型。

```json
"resultMap": { "turn": <int> }
"response":  "Step <int>"
```

---

### `thinking`
模型思维链的流式片段（`reasoning_content`）。多个 `thinking` 事件累积成该轮次的推理缓冲区。

```json
"resultMap": null,
"response":  "<delta string>"
```

渲染器建议：
- 默认：将整个轮次的思考折叠成单行（"💭 Thinking (N 个字符)"）—— Kimi 风格。
- 提供"展开"操作以显示完整文本。
- 如果模型泄露敏感的中间步骤，则完全在标志后面隐藏。

---

### `text`
模型最终答案的流式片段。多个 `text` 事件累积成该轮次的答案缓冲区。

```json
"resultMap": null,
"response":  "<delta string>"
```

等所有片段到达后，在前端渲染为 Markdown 或通过 `rich.markdown.Markdown`（终端）渲染——部分 Markdown 看起来是损坏的。

---

### `tool_call_start`
Handler 即将调用一个工具。

```json
"resultMap": {
  "tool":         "<name>",
  "arguments":    { ... 原始参数字典 ... },
  "tool_call_id": "<id>"
}
"response": "<name>"
```

`tool_call_id` 与 LLM 的 OpenAI 格式 `tool_calls[*].id` 匹配，因此渲染器可以将结果附加到正确的卡片上。

---

### `tool_result`
工具完成（或失败）。

```json
"resultMap": {
  "tool":            "<name>",
  "toolResult":      <string-or-json>,
  "ok":              true | false,
  "elapsed_seconds": <float>,
  "tool_call_id":    "<id>",
  "error_type":      "<class-name>"   // 仅在 ok=false 时存在
}
"response": <toolResult 字符串副本，用于遗留兼容>
```

当 `ok=false` 时，渲染器应将工具标记为红色，并展示 `error_type` 和 `toolResult` 中的消息。

---

### `step_end`
当前 ReAct 迭代完成。

```json
"resultMap": {
  "turn":            <int>,
  "has_tool_calls":  true | false,
  "elapsed_seconds": <float>
}
"response": "Step <turn> done"
```

`has_tool_calls=false` 表示模型只产生了纯文本回复——渲染器可以刷新当前卡片，因为运行即将结束。

---

### `usage`
整个运行的 Token + 耗时总计，只发出一次，在 `result` 之前。

```json
"resultMap": {
  "prompt_tokens":     <int>,
  "completion_tokens": <int>,
  "total_tokens":      <int>,
  "total_seconds":     <float>
}
"response": "Usage: <total_tokens> tokens"
```

---

### `result` (终止事件)
运行成功完成。

```json
"resultMap": {
  "taskSummary": "<最终答案字符串>",
  "result":      "<最终答案字符串>"
}
"response":  "<taskSummary>",
"finished":  true
```

---

### `error` (终止事件)
运行失败。负载镜像 `AgentCoreError.to_dict()`。

```json
"response": {
  "code":        "<error-code>",
  "message":     "<raw-message>",
  "category":    "llm | tool | runtime | unexpected",
  "retryable":   true | false,
  "status_code": <int-or-omitted>,
  "details":     { ... }
}
"resultMap": null,
"errorMsg":  "<message>",
"finished":  true
```

渲染器应通过 `scripts/renderers/friendly_errors.py`（Python）或 `web/src/friendlyErrors.ts`（TypeScript）将 `code` 映射为友好消息。两个文件包含相同的 code → headline/detail/hint 表——添加 code 时**必须**两边同步更新。

标准错误码：

| 错误码                            | 可重试 | 含义                          |
|---------------------------------|--------|----------------------------------|
| `llm_rate_limited`              | 是     | 上游限流了请求。  |
| `llm_timeout`                   | 是     | 模型未及时响应。    |
| `llm_connection_error`          | 是     | 网络或上游不可达。 |
| `llm_stream_error`              | 是     | LLM 的 SSE 流中断。       |
| `llm_context_window_exceeded`   | 否     | 对话过长。           |
| `llm_http_error`                | 视情况而定   | LLM API 返回非 2xx。            |
| `tool_execution_error`          | 视情况而定   | 工具在 `run()` 中抛出异常。      |
| `agent_cancelled`               | 否     | 用户中止了运行。            |
| `runtime_execution_error`       | 否     | 通用运行时故障。           |
| `unexpected_error`              | 否     | 未分类的异常。          |

---

### 遗留 / 兼容类型

这些早于流式协议升级。新代码不应发出它们，但渲染器必须处理它们，以便旧的 Agent（例如 `LegacyHandler` 包装的业务代码）继续工作：

- `task` — `response` 是纯文本 + 尾部换行。
- `tool_thought` — `resultMap.content` 携带内联推理。
- `search_result` — `response` 携带任意搜索负载。
- `final_result` — 与 `result` 形状相同；两者都视为终止事件。
- `done` — 终止的"流已关闭"标记；`response = "任务完成"`。

---

## 添加新的事件类型

1. 将值添加到 `agent_core.stream.events.EventType`（Python）和 `web/src/types.ts` 的 `ResponseType`（TypeScript）。
2. 在 `Printer` 上添加便利方法，并在 `_build_response` 中添加路由。
3. 如果事件映射自运行时概念，添加 `RuntimeEvent` 子类并通过 `Printer.from_runtime_event` 接入。
4. 更新两个渲染器（`scripts/renderers/rich_renderer.py` 和 `web/src/App.tsx` / `traceReducer.ts`）。
5. 向 `tests/fixtures/sse_golden/<name>.jsonl` 添加黄金用例，并在 `tests/test_sse_golden_compatibility.py` 中添加参数化条目。
6. 更新本文档。

---

## 分层职责

```
   ┌───────────────────────────────────────┐
   │  应用代码                               │
   │   （你的 Agent 或 React UI）              │
   └────────────────┬──────────────────────┘
                    │
                    ▼  EventType / responseType
   ┌───────────────────────────────────────┐  流式协议
   │  Printer + EventStream                │  （本文档）
   │   将每个字典塑造成相同格式                │
   └────────────────┬──────────────────────┘
                    │
                    ▼  RuntimeEvent
   ┌───────────────────────────────────────┐  内部事件模型 ——
   │  TurnRunner + ToolExecutor            │  只要 Printer 保持
   │   语义生命周期标记                       │  传输层形状稳定，就可以自由演进
   └───────────────────────────────────────┘
```

双层结构——内部诊断用 `RuntimeEvent`，传输用 `EventType`——的存在使得内部生命周期模型可以增长（更丰富的错误状态、重试遥测、子轮次事件），而不会破坏渲染器。
