# Streaming Protocol v2

Agent Core exposes runtime output as standard Server-Sent Events. Runtime code
emits semantic `RuntimeEvent` objects; `SseSink` renders those objects into SSE
v2 frames.

## Wire Format

Each frame uses named SSE events:

```text
event: text
data: {"delta": "hello", "run_id": "run_...", "turn_id": "turn_...", "request_id": "web-...", "conversation_id": "web-conversation"}

event: tool_result
data: {"tool": "read_file", "ok": true, "result": "...", "elapsed_ms": 42, "tool_call_id": "call_1"}

event: done
data: {"reason": "completed", "result": "...", "elapsed_ms": 1234}
```

The stream may also include metadata comments:

```text
: run_id=run_abc turn_id=turn_def request_id=web-123 conversation_id=web-conversation
```

HTTP responses include:

- `X-Streaming-Protocol: agent-core.sse.v2`
- `X-Request-ID: <request-id>`
- `X-Conversation-ID: <conversation-id>`

## Event Order

Typical ReAct run:

```text
start
step
thinking*
text*
tool_call_start?
tool_result?
step_end
...
usage
done
```

Failures end with `error` instead of `done`.

## Event Data

### `start`

```json
{
  "query": "user prompt",
  "agent": "general_chat",
  "run_id": "run_...",
  "turn_id": "turn_...",
  "request_id": "web-...",
  "conversation_id": "web-conversation"
}
```

### `step`

```json
{ "turn": 1 }
```

### `thinking`

Reasoning / model thought delta.

```json
{ "delta": "..." }
```

### `text`

Assistant answer delta.

```json
{ "delta": "..." }
```

### `tool_call_start`

```json
{
  "tool": "read_file",
  "arguments": { "path": "README.md" },
  "tool_call_id": "call_1"
}
```

### `tool_result`

```json
{
  "tool": "read_file",
  "ok": true,
  "result": "file contents or summary",
  "elapsed_ms": 42,
  "tool_call_id": "call_1"
}
```

Failed tool calls set `ok: false` and may include `error_type` and
`error_message`.

### `step_end`

```json
{
  "turn": 1,
  "has_tool_calls": true,
  "elapsed_ms": 500
}
```

### `usage`

```json
{
  "prompt_tokens": 100,
  "completion_tokens": 50,
  "total_tokens": 150,
  "total_seconds": 2.5,
  "elapsed_ms": 2500
}
```

### `done`

```json
{
  "reason": "completed",
  "result": "final answer summary",
  "elapsed_ms": 1234
}
```

### `error`

The payload is `AgentCoreError.to_dict()` when available:

```json
{
  "code": "llm_timeout",
  "message": "provider timed out",
  "category": "llm",
  "retryable": true,
  "details": {}
}
```

## Compatibility Notes

v1 used one unnamed SSE message containing a large JSON envelope:

```json
{
  "responseType": "text",
  "response": "...",
  "responseAll": "",
  "useTimes": 0,
  "resultMap": null
}
```

v2 removes the envelope from the live protocol. Consumers should switch on the
SSE `event:` name and read the compact `data` object. The terms
`responseType`, `responseAll`, `useTimes`, `resultMap`, and `errorMsg` are no
longer emitted by `Printer`, `SseSink`, the web endpoint, or the CLI renderer.

The event names `task`, `tool_thought`, `search_result`, and `final_result`
remain reserved for streaming tools. They are still delivered as v2 named
events with compact `data` payloads.
