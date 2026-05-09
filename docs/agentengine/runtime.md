# runtime — 执行引擎与生命周期

> `src/agentengine/runtime/` 是 AgentEngine 的心脏。它包含唯一的 think→act 循环 `run_turn()`、生命周期管理者 `TurnRunner`、运行时事件模型 `RuntimeEvent`、以及运行状态机 `RunState`。

---

## 模块组成

```
runtime/
├── turn.py              # run_turn() — 唯一的 ReAct 循环
├── turn_runner.py       # TurnRunner — 生命周期 + JSONL 日志 + 中间件
├── events.py            # RuntimeEvent 家族 — 内部语义事件
├── run_state.py         # RunState + RunStatus + TerminalReason
├── sinks.py             # RuntimeEventFanout — 事件分发
├── file_access_tracker.py  # 工具文件访问追踪
└── __init__.py
```

---

## run_turn() — 唯一的 think→act 循环

**文件：** `src/agentengine/runtime/turn.py`

### 设计意图

`run_turn()` 是唯一的 ReAct 循环入口，所有 Agent 共享同一套循环逻辑。差异通过**数据**（AgentSpec）表达，而非通过**子类**。

```python
async def run_turn(
    agent: AgentRun,
    context: AgentContext,
    query: str,
    *,
    tool_timeout_seconds: float | None = DEFAULT_TOOL_TIMEOUT_SECONDS,
    emit: EmitFn | None = None,
) -> str:
    ...
```

### 执行流程

```
run_turn(agent, context, query)
    │
    ├──► agent.setup()              # 注册工具、初始化
    ├──► state = RUNNING
    ├──► memory.load_from_db()      # 恢复历史对话
    │
    ├──► _loop()                    # ━━━ ReAct 循环 ━━━
    │      │
    │      ├──► emit TurnStarted
    │      ├──► _build_messages()   # memory.snapshot() + next_step_prompt
    │      ├──► _chat_streaming()   # LLM 流式调用
    │      │      ├──► 边收边 emit TextDelta / ReasoningDelta
    │      │      └──► 按 index 累积 tool_calls
    │      ├──► memory.add_assistant_message()
    │      ├──► if tool_calls:
    │      │      └──► _execute_tool_calls()  # 逐个执行工具
    │      ├──► emit TurnEnded
    │      └──► if no tool_calls: break
    │
    ├──► emit UsageReport
    ├──► state = FINISHED
    ├──► memory.save_to_db()        # 持久化对话
    └──► agent.teardown()           # 清理资源
```

### 核心函数详解

#### `_loop()` — ReAct 循环体

```python
for _ in range(agent.max_steps):
    agent.current_step += 1
    emit(TurnStarted(...))

    messages = _build_messages(agent, next_step)
    tools = context.tool_collection.to_openai_tools()
    response = await _chat_streaming(context, messages, tools=tools, emit=emit, ...)

    # 累积 Token 用量
    if response.usage:
        prompt_tokens_total += response.usage["prompt_tokens"]
        completion_tokens_total += response.usage["completion_tokens"]

    # 写入记忆
    agent.memory.add_assistant_message(
        response.content or "",
        reasoning_content=response.reasoning_content or "",
        tool_calls=response.tool_calls or None,
    )

    if response.tool_calls:
        await _execute_tool_calls(agent, context, response.tool_calls, emit=emit, ...)
        emit(TurnEnded(..., has_tool_calls=True))
        continue   # 还有工具要执行，再来一轮
    else:
        emit(TurnEnded(..., has_tool_calls=False))
        break      # LLM 直接回答了，结束循环

emit(UsageReport(...))
return final_answer
```

#### `_chat_streaming()` — 流式 LLM 调用

```python
async for chunk in chat_stream(messages, tools=tools):
    if chunk.content:
        emit(TextDelta(content=chunk.content))
    if chunk.reasoning_content:
        emit(ReasoningDelta(content=chunk.reasoning_content))
    if chunk.raw:
        _accumulate_tool_calls(tool_calls_map, chunk.raw)

return LLMResponse(
    content="".join(content_parts),
    reasoning_content="".join(reasoning_parts),
    tool_calls=list(tool_calls_map.values()),
    ...
)
```

**关键设计：** `tool_calls` 在 stream 收集阶段就按 `index` 拼好了，Handler 永远只看到完整调用。

#### `_execute_tool_calls()` — 工具执行

对每个 tool_call：
1. 解析参数（`json.loads`）
2. 从 `tool_collection` 查找工具
3. **审批门检查**（破坏性工具 → ApprovalGate）
4. **配额检查**（QuotaStore）
5. **Hook 触发**（`PRE_TOOL_USE`）
6. `ToolExecutor.execute(tool, args)`
7. **Hook 触发**（`POST_TOOL_USE`）
8. 结果写入 memory

### 异常处理

```python
try:
    result = await _loop(...)
    agent.state = AgentState.FINISHED
except asyncio.CancelledError:
    agent.state = AgentState.CANCELLED
    raise
except Exception:
    agent.state = AgentState.ERROR
    raise
finally:
    await _teardown(agent, context, primary_error)
```

无论成功、失败、取消，都会执行 teardown 和 memory 持久化。

---

## TurnRunner — 生命周期管理者

**文件：** `src/agentengine/runtime/turn_runner.py`

### 职责

TurnRunner 是 `run_turn()` 的「包装器」，负责：
- 生成 `run_id` / `turn_id`
- 状态机转换（`RunState`）
- JSONL 事件日志（`RunEventLog`）
- 中间件链执行（`MiddlewareChain`）
- 生命周期 Hook（`SessionStart` / `Stop`）
- 错误分类（`TerminalReason`）

```python
runner = TurnRunner(
    session_id="...",
    log_dir="logs",
    enable_event_log=True,
    middleware=MiddlewareChain([...]),
    hook_manager=HookManager(),
)

result = await runner.run(
    agent=agent,
    context=context,
    query=query,
    on_event=async_callback,   # 事件回调（通常接 SseSink）
    tool_timeout_seconds=30.0,
)
```

### 状态机：RunState

```
PENDING ──mark_running()──► RUNNING
    │                          │
    │                          ├── 正常结束 ──► mark_completed() ──► COMPLETED
    │                          ├── 异常 ──────► mark_failed(reason) ──► FAILED
    │                          └── 取消 ──────► mark_cancelled() ──► CANCELLED
    │
    └── 如果 SessionStart Hook 中止，直接 FAILED
```

### 错误分类

| 异常类型 | TerminalReason |
|----------|---------------|
| `LLMContextWindowError` | `context_exceeded` |
| `LLMError` | `model_failed` |
| `ToolExecutionError` | `tool_failed` |
| `ApprovalDeniedError` | `tool_failed` |
| `QuotaExceededError` | `quota_exceeded` |
| 其他 | `runtime_failed` |

---

## RuntimeEvent — 内部语义事件

**文件：** `src/agentengine/runtime/events.py`

### 设计意图

与 `stream/events.py` 的 `EventType` **刻意分层**：
- `RuntimeEvent` = 后端诊断语义（丰富、可演进）
- `EventType` = 前端公共协议（稳定、精简）

```python
@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    run_id: str
    turn_id: str
    timestamp: datetime = field(default_factory=_utc_now)
    event_type: ClassVar[str] = "runtime_event"

    def to_dict(self) -> dict[str, Any]:
        ...
```

### 事件家族

| 事件类 | event_type | 触发时机 |
|--------|-----------|---------|
| `RunStarted` | `run_started` | TurnRunner.run() 开始 |
| `RunCompleted` | `run_completed` | 正常结束 |
| `RunFailed` | `run_failed` | 异常结束 |
| `RunCancelled` | `run_cancelled` | 被取消 |
| `TurnStarted` | `turn_started` | 第 N 轮循环开始 |
| `TurnEnded` | `turn_ended` | 第 N 轮循环结束 |
| `TextDelta` | `text_delta` | 收到内容 chunk |
| `ReasoningDelta` | `reasoning_delta` | 收到推理 chunk |
| `ToolCallStarted` | `tool_call_started` | 工具开始执行 |
| `ToolCallCompleted` | `tool_call_completed` | 工具执行成功 |
| `ToolCallFailed` | `tool_call_failed` | 工具执行失败 |
| `ToolStreamEventEmitted` | `tool_stream_event` | StreamingTool 中间事件 |
| `UsageReport` | `usage_report` | 循环结束，汇报用量 |
| `ApprovalRequired` | `approval_required` | 破坏性工具等待审批 |
| `TodosUpdated` | `todos_updated` | TodoWriteTool 更新清单 |
| `UserQuestionAsked` | `user_question_asked` | AskUserQuestionTool 提问 |

所有事件都支持 `to_dict()` 序列化，用于 JSONL 日志。

---

## RunState + TerminalReason

**文件：** `src/agentengine/runtime/run_state.py`

```python
class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class TerminalReason(str, Enum):
    NORMAL = "normal"
    TOOL_FAILED = "tool_failed"
    MODEL_FAILED = "model_failed"
    CONTEXT_EXCEEDED = "context_exceeded"
    RUNTIME_FAILED = "runtime_failed"
    QUOTA_EXCEEDED = "quota_exceeded"
    CANCELLED = "cancelled"
```

`RunState` 是可变 dataclass，记录一次运行的完整生命周期：

```python
@dataclass(slots=True)
class RunState:
    run_id: str
    session_id: str
    turn_id: str
    status: RunStatus = RunStatus.PENDING
    terminal_reason: TerminalReason | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
```

---

## 事件分发：RuntimeEventFanout

**文件：** `src/agentengine/runtime/sinks.py`

```python
fanout = RuntimeEventFanout([
    JsonlSink(event_log),   # 写入 JSONL 文件
    on_event_callback,       # 传给 Service → SseSink → 前端
])

await fanout.consume(event)
```

每个事件会被并行分发给所有 sink。

---

## 文件访问追踪：TurnFileAccessTracker

**文件：** `src/agentengine/runtime/file_access_tracker.py`

追踪一次运行中 read/write/edit 工具访问了哪些文件。用于：
- **安全校验**：edit 工具拒绝覆盖未被 read 过的文件
- **审计**：知道一次运行触及了哪些路径

在 TurnRunner 中自动注入到 `context.extras["file_access_tracker"]`。

---

## 代码示例：直接调用 run_turn

```python
from agentengine.base.agent import AgentRun
from agentengine.base.context import AgentContext
from agentengine.runtime.turn import run_turn
from agentengine.spec import AgentSpec

spec = AgentSpec(name="demo", system_prompt="You are a demo.")
context = AgentContext(request_id="r1", query="hello")
agent = AgentRun(spec=spec, context=context)

# 直接驱动循环（没有 TurnRunner 的生命周期包装）
result = await run_turn(agent, context, "hello", emit=print)
```

---

## 关联文档

- [base.md](base.md) — AgentRun、AgentContext 的定义
- [stream.md](stream.md) — Printer 如何将 RuntimeEvent 转成 SSE
- [observability.md](observability.md) — JSONL 日志详情
- [enterprise.md](enterprise.md) — 中间件、审批门、配额
- [hooks.md](hooks.md) — 生命周期钩子
