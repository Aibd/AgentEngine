# stream — 事件流与 SSE 桥接

> `src/agentengine/stream/` 负责将内部运行时事件翻译成前端可消费的 SSE（Server-Sent Events）流。它是框架与外部世界之间的「窗口」。

---

## 模块组成

```
stream/
├── events.py        # EventType — 前端公共协议事件名称
├── printer.py       # Printer — RuntimeEvent → SSE v2 帧
├── sse_queue.py     # SseEventQueue — 异步事件队列
├── sse_sink.py      # SseSink — 消费 RuntimeEvent 并驱动 Printer
└── __init__.py
```

---

## 设计哲学：双轨事件系统

```
┌─────────────────────────────────────────────────────────────┐
│                    后端（agentengine）                        │
│  ┌─────────────┐        ┌─────────────┐                     │
│  │ RuntimeEvent │ ─────►│   Printer   │                     │
│  │  (丰富语义)  │        │ (翻译层)    │                     │
│  └─────────────┘        └──────┬──────┘                     │
│                                 │                           │
│                        ┌────────┴────────┐                 │
│                        ▼                 ▼                 │
│                   ┌─────────┐      ┌──────────┐            │
│                   │ JsonlSink│      │ SseSink  │            │
│                   │ (日志)   │      │ (前端)   │            │
│                   └─────────┘      └────┬─────┘            │
│                                         │                   │
└─────────────────────────────────────────┼───────────────────┘
                                          │ SSE v2 流
                                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    前端（Web / CLI）                          │
│  ┌─────────────┐        ┌─────────────┐                     │
│  │ traceReducer │ ◄─────│  SSE Transport│                    │
│  │  (状态归约)  │        │  (fetch+解析) │                    │
│  └──────┬──────┘        └─────────────┘                     │
│         │                                                   │
│         ▼                                                   │
│  ┌─────────────────────────────────────┐                    │
│  │  UI: step / thinking / tool / final │                    │
│  └─────────────────────────────────────┘                    │
└─────────────────────────────────────────────────────────────┘
```

**核心原则：**
- `RuntimeEvent`（`runtime/events.py`）是后端内部语义，可以自由演进
- `EventType`（`stream/events.py`）是前端公共协议，必须保持稳定
- `Printer` 是两者之间的标准翻译层

---

## EventType — 前端公共协议

**文件：** `src/agentengine/stream/events.py`

```python
class EventType(str, Enum):
    # 生命周期
    START = "start"
    DONE = "done"

    # 轮次边界
    STEP = "step"
    STEP_END = "step_end"

    # 模型输出
    THINKING = "thinking"
    TEXT = "text"

    # 工具生命周期
    TOOL_CALL_START = "tool_call_start"
    TOOL_RESULT = "tool_result"

    # 遥测
    USAGE = "usage"

    # 兼容性事件（流式工具用）
    TASK = "task"
    TOOL_THOUGHT = "tool_thought"
    SEARCH_RESULT = "search_result"
    FINAL_RESULT = "final_result"

    # 会话侧通道（内置工具发出）
    TODOS_UPDATED = "todos_updated"
    USER_QUESTION_ASKED = "user_question_asked"

    # 终止
    ERROR = "error"
```

这些字符串值就是 SSE 的 `event:` 行内容。前端按 event 名称分发即可。

---

## Printer — 翻译层

**文件：** `src/agentengine/stream/printer.py`

### 职责

Printer 接收 `RuntimeEvent`，输出标准化的 SSE 帧：

```json
{
  "event": "text",
  "data": {"delta": "hello", "request_id": "req-1", "conversation_id": "conv-1"}
}
```

### from_runtime_event — 标准翻译

```python
async def from_runtime_event(self, event: RuntimeEvent) -> None:
    if isinstance(event, RunStarted):
        await self._runtime_frame(EventType.START, event, {
            "query": event.input_summary,
            "agent": event.agent_name,
        })
    elif isinstance(event, TextDelta):
        await self._runtime_frame(EventType.TEXT, event, {
            "delta": event.content,
        })
    elif isinstance(event, ToolCallStarted):
        await self._runtime_frame(EventType.TOOL_CALL_START, event, {
            "tool": event.tool_name,
            "arguments": event.arguments,
            "tool_call_id": event.tool_call_id,
        })
    elif isinstance(event, ToolCallCompleted):
        await self._runtime_frame(EventType.TOOL_RESULT, event, {
            "tool": event.tool_name,
            "ok": True,
            "result": event.result_summary,
            "elapsed_ms": ...,
            "tool_call_id": event.tool_call_id,
        })
    # ... 更多映射
```

### 便捷方法

Printer 也提供直接发送特定事件的便捷方法：

```python
await printer.start(query="hello")
await printer.text("Hello")
await printer.thinking("Let me think...")
await printer.tool_call_start("read_file", {"path": "README.md"})
await printer.tool_result("read_file", "# Title", ok=True)
await printer.step(turn=1)
await printer.step_end(turn=1, has_tool_calls=True)
await printer.usage(prompt_tokens=100, completion_tokens=50)
await printer.result({"result": "Done"})
await printer.error("Something went wrong")
```

### 元数据注入

所有事件自动携带 `request_id` 和 `conversation_id`：

```python
def _with_base_meta(self, data, *, run_id="", turn_id=""):
    payload = dict(data) if isinstance(data, dict) else {"value": data}
    payload.setdefault("request_id", self.request_id)
    payload.setdefault("conversation_id", self.conversation_id)
    if run_id:
        payload.setdefault("run_id", run_id)
    if turn_id:
        payload.setdefault("turn_id", turn_id)
    return payload
```

---

## SSE v2 传输格式

**文件：** `docs/STREAMING_PROTOCOL.md`（完整规范）

每帧使用命名的 SSE 事件：

```text
event: text
data: {"delta": "hello", "run_id": "run_...", "turn_id": "turn_...", "request_id": "web-...", "conversation_id": "web-conversation"}

event: tool_result
data: {"tool": "read_file", "ok": true, "result": "...", "elapsed_ms": 42, "tool_call_id": "call_1"}

event: done
data: {"reason": "completed", "result": "...", "elapsed_ms": 1234}
```

元数据注释：

```text
: run_id=run_abc turn_id=turn_def request_id=web-123 conversation_id=web-conversation
```

HTTP 响应头：

```text
X-Streaming-Protocol: agent-core.sse.v2
X-Request-ID: <request-id>
X-Conversation-ID: <conversation-id>
```

---

## 典型事件序列

### 单轮无工具

```text
start ──► step ──► thinking* ──► text* ──► step_end ──► usage ──► done
```

### 两轮带工具

```text
start ──► step ──► thinking ──► tool_call_start ──► tool_result ──► step_end
                                                                          │
                                ▼─── (loop) ────────────────────────────┘
                                step ──► text ──► step_end ──► usage ──► done
```

### 失败

```text
start ──► step ──► ... ──► error
```

---

## 前端接入示例

### JavaScript / TypeScript

```typescript
const eventSource = new EventSource('/api/runs/stream?query=...&agent_name=general_chat');

eventSource.addEventListener('start', (e) => {
  const data = JSON.parse(e.data);
  console.log('Run started:', data.agent, data.query);
});

eventSource.addEventListener('text', (e) => {
  const data = JSON.parse(e.data);
  appendText(data.delta);  // 增量追加
});

eventSource.addEventListener('tool_call_start', (e) => {
  const data = JSON.parse(e.data);
  showToolCard(data.tool, data.arguments);
});

eventSource.addEventListener('tool_result', (e) => {
  const data = JSON.parse(e.data);
  updateToolCard(data.tool, data.result, data.ok);
});

eventSource.addEventListener('done', (e) => {
  const data = JSON.parse(e.data);
  finalize(data.result);
  eventSource.close();
});

eventSource.addEventListener('error', (e) => {
  const data = JSON.parse(e.data);
  showError(data.code, data.message);
  eventSource.close();
});
```

### Python（异步迭代）

```python
from services.agent_orchestration_service import AgentOrchestrationService

service = AgentOrchestrationService()
context, event_stream = service.create_streaming_context(
    request_id="r1", query="hello", conversation_id="c1"
)
context.llm = llm

async def run():
    task = asyncio.create_task(
        service.run(agent_name="general_chat", query="hello", context=context)
    )
    async for event in event_stream:
        print(f"[{event['event']}] {event['data']}")
    result = await task
```

---

## 关联文档

- [STREAMING_PROTOCOL.md](../STREAMING_PROTOCOL.md) — SSE v2 完整规范
- [runtime.md](runtime.md) — RuntimeEvent 的产生端
- [guides/streaming-integration.md](../guides/streaming-integration.md) — 自定义前端接入指南
