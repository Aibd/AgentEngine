# 指南：接入自定义前端

> 如何消费 AgentEngine 的 SSE v2 流，构建自己的 UI。

---

## 关键概念：run() 返回值 vs 流式输出

`engine.run()` 的返回类型**始终是 `str`**（完整最终回答），会等待整个 agent turn 结束后才返回：

```python
answer = await engine.run(agent_name="support", query="帮我查订单", context=context)
# answer 是完整的最终文本，不是流式增量
```

流式是**旁路通道**——LLM 的 token 增量通过 `SseEventQueue`（或 `on_event` 回调）实时推送，与 `run()` 并行运行：

```python
task = asyncio.create_task(engine.run(..., context=context))
async for frame in event_stream:
    # 实时收到 token 增量、工具调用等事件
    send_sse_to_client(frame)
answer = await task  # 完整文本
```

这种设计的好处：
- **Web SSE**：前端实时渲染 token，同时后端持有完整结果用于持久化
- **CLI**：实时打印 token，结束后统一处理（如保存日志）
- **测试**：可以直接 `await engine.run()` 拿到完整断言内容

---

## 获取 SSE 流

### 方式 1：使用 AgentEngine

```python
from agentengine import AgentEngine

service = AgentEngine(presets=presets, llm_factory=make_llm)
context, event_stream = service.create_streaming_context(
    request_id="web-123",
    query="你好",
    conversation_id="conv-456",
)
context.llm = llm

# 在后台运行 Agent
task = asyncio.create_task(
    service.run(agent_name="general_chat", query="你好", context=context)
)

# 消费事件流
async for event in event_stream:
    print(event)
    # {'event': 'start', 'data': {...}}
    # {'event': 'text', 'data': {'delta': 'Hello'}}
    # {'event': 'done', 'data': {'reason': 'completed', 'result': '...'}}
```

### 方式 2：Web API

项目自带 FastAPI 参考后端 (`examples/reference_app/services/web_api.py`)：

```
GET /api/runs/stream?query=...&agent_name=...&conversation_id=...
```

返回 `text/event-stream`。

---

## SSE 事件格式

每个事件：

```text
event: <EventType>
data: <JSON payload>
```

### 元数据注释

```text
: run_id=run_abc turn_id=turn_def request_id=web-123 conversation_id=web-conversation
```

---

## 事件参考

| event | 说明 | data 字段 |
|-------|------|----------|
| `start` | 运行开始 | `query`, `agent`, `run_id`, `turn_id` |
| `step` | 第 N 轮开始 | `turn` |
| `thinking` | 推理增量 | `delta` |
| `text` | 回答增量 | `delta` |
| `tool_call_start` | 工具开始 | `tool`, `arguments`, `tool_call_id` |
| `tool_result` | 工具结果 | `tool`, `ok`, `result`, `elapsed_ms`, `tool_call_id`, `error_type?` |
| `step_end` | 轮次结束 | `turn`, `has_tool_calls`, `elapsed_ms` |
| `usage` | 用量报告 | `prompt_tokens`, `completion_tokens`, `total_tokens`, `total_seconds` |
| `done` | 成功结束 | `reason`, `result`, `elapsed_ms` |
| `error` | 失败结束 | `code`, `message`, `category`, `retryable`, `details` |
| `todos_updated` | 任务清单 | `todos` |
| `user_question_asked` | 用户提问 | `question_id`, `question`, `options`, `multiple` |

---

## 前端状态管理示例

### React + TypeScript

```typescript
type SseEvent = {
  event: string;
  data: Record<string, any>;
};

type RunState = {
  status: 'idle' | 'running' | 'completed' | 'error';
  turns: Turn[];
  currentTurn: Turn | null;
  finalResult: string;
  error: ErrorPayload | null;
  usage: UsagePayload | null;
};

function useAgentStream() {
  const [state, dispatch] = useReducer(runReducer, initialState);

  const run = async (query: string, agentName: string) => {
    const response = await fetch(
      `/api/runs/stream?query=${encodeURIComponent(query)}&agent_name=${agentName}`
    );

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      // 解析 SSE
      let currentEvent = '';
      let currentData = '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7);
        } else if (line.startsWith('data: ')) {
          currentData = line.slice(6);
        } else if (line === '' && currentEvent) {
          dispatch({
            type: currentEvent,
            payload: JSON.parse(currentData),
          });
          currentEvent = '';
          currentData = '';
        }
      }
    }
  };

  return { state, run };
}
```

### Reducer 核心逻辑

```typescript
function runReducer(state: RunState, action: Action): RunState {
  switch (action.type) {
    case 'start':
      return { ...state, status: 'running', turns: [] };

    case 'step':
      const turn: Turn = { number: action.payload.turn, events: [] };
      return {
        ...state,
        turns: [...state.turns, turn],
        currentTurn: turn,
      };

    case 'thinking':
      // 追加到当前 turn 的 thinking
      return appendToCurrentTurn(state, 'thinking', action.payload.delta);

    case 'text':
      // 追加到当前 turn 的 text
      return appendToCurrentTurn(state, 'text', action.payload.delta);

    case 'tool_call_start':
      return appendToCurrentTurn(state, 'toolCalls', {
        id: action.payload.tool_call_id,
        tool: action.payload.tool,
        arguments: action.payload.arguments,
        status: 'running',
      });

    case 'tool_result':
      return updateToolCall(state, action.payload.tool_call_id, {
        status: action.payload.ok ? 'completed' : 'failed',
        result: action.payload.result,
        elapsedMs: action.payload.elapsed_ms,
      });

    case 'done':
      return { ...state, status: 'completed', finalResult: action.payload.result };

    case 'error':
      return { ...state, status: 'error', error: action.payload };

    default:
      return state;
  }
}
```

---

## 终端渲染参考

项目自带 `scripts/renderers/rich_renderer.py`，可作为终端渲染的参考实现：

```python
from scripts.renderers.rich_renderer import RichRenderer

renderer = RichRenderer(show_reasoning=True)
renderer.on_event(event)  # 传入 SSE event dict
```

---

## 关联文档

- [STREAMING_PROTOCOL.md](../STREAMING_PROTOCOL.md) — SSE v2 完整规范
- [stream.md](../agentengine/stream.md) — Printer 和 EventType 详解
