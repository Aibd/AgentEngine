# AgentEngine 架构详解

> 图文并茂的完整架构拆解。看完这一篇，你应该能回答：**这个项目在做什么、由哪些零件组成、一条用户请求如何穿过它、我该从哪里下手扩展。**

---

## 1. 整体分层（一张图看全）

```
                       ┌──────────────────────────────────┐
                       │   你的应用 / CLI / Web / SSE      │
                       │   run_agent.py · API endpoint    │
                       └────────────────┬─────────────────┘
                                        │ service.run("general_chat", query, ctx)
                                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  ⓪ services/   AgentOrchestrationService — 应用入口                      │
│      查 agents.REGISTRY → 创建 AgentRun → 交给 TurnRunner                │
└─────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  ① runtime/    TurnRunner + RunState + RuntimeEvent                     │
│      生成 run_id/turn_id, 记录生命周期事件 → JSONL 日志                   │
└─────────────────────────────────────────────────────────────────────────┘
                                        │ handler.handle(agent, ctx, query)
                                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  ② runtime/turn.py  run_turn() — 唯一的 think→act 循环                  │
└─────────────────────────────────────────────────────────────────────────┘
                          │            │            │
                          ▼            ▼            ▼
┌───────────────────┐ ┌──────────┐ ┌───────────┐ ┌────────────────────┐
│ ③ base/           │ │ ④ llm/   │ │ ⑤ memory/ │ │ ⑥ tools/           │
│ BaseAgent         │ │ OpenAI-  │ │ Message · │ │ Tool · Collection ·│
│ AgentContext      │ │ Compat   │ │ Memory    │ │ Registry · Executor│
│ AgentState        │ │ Client   │ │ (自动裁剪) │ │ + SkillTool 等     │
└───────────────────┘ └──────────┘ └───────────┘ └────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  ⑦ stream/     EventStream · Printer · EventType                        │
│      把内部事件 → 前端 SSE 信封 (responseType / response / finished …)    │
└─────────────────────────────────────────────────────────────────────────┘

辅助层:
  prompts/       PromptLoader  (读 YAML 提示词，带缓存)
  skills/        SkillLoader   (扫 .agent/skills/SKILL.md)
  observability/ RunEventLog   (logs/runs/<日期>/<run_id>.jsonl)
  errors.py      AgentEngineError 家族 (LLMError / ToolExecutionError …)
  enterprise/    ApprovalGate · QuotaStore · MiddlewareChain · TenantContext
  hooks/         HookManager · HookEvent · HookPayload
  persistence/   PersistencePort · SQLite 实现
  concurrency/   ConversationLockManager
```

**记忆口诀：** `services → runtime → (base · llm · memory · tools) → stream`，左到右就是一条请求的旅行路线。

---

## 2. 一条请求的完整旅行（时序图）

```
用户              Service             TurnRunner          run_turn()          LLM Client          ToolExecutor        Printer
 │                   │                   │                   │                   │                   │                  │
 │  run("general_chat", query)           │                   │                   │                   │                  │
 ├──────────────────►│                   │                   │                   │                   │                  │
 │                   │ _resolve_spec()                       │                   │                   │                  │
 │                   │ AgentRun(spec,ctx)                    │                   │                   │                  │
 │                   │ TurnRunner.run() ►│                   │                   │                   │                  │
 │                   │                   │ 生成 run_id        │                   │                   │                  │
 │                   │                   │ emit RunStarted   │                   │                   │                  │
 │                   │                   │ handler.handle() ►│                   │                   │                  │
 │                   │                   │                   │ agent.setup()    │                   │                  │
 │                   │                   │                   │ state = RUNNING  │                   │                  │
 │                   │                   │                   │                                                            │
 │                   │             ┌─────┤  ━━━ 循环开始 (最多 max_steps 次) ━━━                                          │
 │                   │             │     │                   │                   │                   │                  │
 │                   │             │     │ build messages    │                   │                   │                  │
 │                   │             │     │ chat_stream()    ►│                   │                   │                  │
 │                   │             │     │                   │ stream chunks ───►│                   │                  │
 │                   │             │     │                   │                   │  TEXT 事件(增量) │                  │
 │                   │             │     ├─────────────────────────────────────────────────────────────────────────────►│
 │                   │             │     │ assemble LLMResponse                  │                   │                  │
 │                   │             │     │ memory.add_assistant_message()        │                   │                  │
 │                   │             │     │                   │                   │                   │                  │
 │                   │             │     │ if tool_calls:    │                   │                   │                  │
 │                   │             │     │   for each call:  │                   │                   │                  │
 │                   │             │     │     executor.execute(tool, args) ──────────────────────►│                   │
 │                   │             │     │                                                            │ tool.run(**args)│
 │                   │             │     │                                                            │ wait_for(timeout)│
 │                   │             │     │     ToolCallStarted/Completed/Failed RuntimeEvent ◄─────│                  │
 │                   │             │     │     memory.add_tool_message()                              │                  │
 │                   │             │     │                                                                │ TOOL_RESULT  │
 │                   │             │     ├─────────────────────────────────────────────────────────────────────────────►│
 │                   │             │     │   continue loop                                            │                  │
 │                   │             │     │                                                            │                  │
 │                   │             │     │ else (no tool_calls):                                      │                  │
 │                   │             │     │   break ━━━ 循环结束 ━━━                                   │                  │
 │                   │             └─────►                   │                   │                   │                  │
 │                   │                   │ return final_answer                                        │                  │
 │                   │                   │                   │ printer.send(RESULT, ..., finished=True)                  │
 │                   │                   │                   ├──────────────────────────────────────────────────────────►│
 │                   │                   │                   │ agent.teardown()                                          │
 │                   │                   │ emit RunCompleted │                                                            │
 │                   │ ◄─────────────────┤                                                                                │
 │ ◄─────────────────┤                                                                                                    │
 │  返回 final_answer (字符串) + 通过 EventStream 持续推送 SSE 给前端                                                       │
```

**两个关键不要混：**

| 通道 | 类型 | 给谁看 | 例子 |
|------|------|--------|------|
| `EventStream`（前端 SSE） | `EventType` | 终端用户 / Web | `text` / `tool_result` / `result` / `error` |
| `RuntimeEvent`（内部） | `RuntimeEvent` 子类 | 后端日志 / 监控 | `RunStarted` / `ToolCallStarted` / `UsageReport` / `RunCompleted` |

它们刻意分层：前端契约不会因为内部加新事件而被打破。

---

## 3. 核心抽象：AgentSpec + AgentRun

```
┌──────────────────────────────────┐        ┌──────────────────────────────────┐
│         AgentSpec  (不可变配置)    │        │       AgentRun  (运行状态)        │
│  frozen=True, slots=True         │        │  slots=True (可变)               │
│                                  │        │                                  │
│  • name                          │        │  • spec: AgentSpec               │
│  • system_prompt                 │  ─────►│  • context: AgentContext         │
│  • next_step_prompt              │        │  • memory: Memory                │
│  • max_steps                     │        │  • current_step: int             │
│  • max_messages                  │        │  • state: AgentState             │
│  • setup: async (ctx)->None      │        │                                  │
│  • teardown: async (ctx)->None   │        │  await agent.setup()   # 委托    │
└──────────────────────────────────┘        └──────────────────────────────────┘
        声明性的"我是谁"                           运行时的"现在在哪"
```

- **AgentSpec** 是 `frozen=True` 的 dataclass——声明"这个 Agent 是什么"（提示词、步数上限、setup/teardown 钩子）。它与运行无关，可跨请求共享。
- **AgentRun** 是可变的 per-run 容器——持有 Memory、当前步数、AgentState。每次请求创建一个新实例。

写一个新 Agent 只需要：

```python
# agents/my_agent/spec.py
SPEC = AgentSpec(
    name="my_agent",
    system_prompt="你是一个 ...",
    max_steps=10,
    setup=_my_setup,  # async (ctx: AgentContext) -> None
)

# agents/__init__.py — 加一行
REGISTRY["my_agent"] = SPEC
```

---

## 4. ReAct 循环细节（代码视角）

`run_turn()._loop` 在 `src/agentengine/runtime/turn.py` 里：

```
┌───────────────────────────────────────────────────────────────┐
│ for step in range(agent.max_steps):                            │
│                                                                │
│   ① messages = memory.snapshot() + next_step_prompt           │
│   ② tools    = context.tool_collection.to_openai_tools()      │
│   ③ resp     = await llm.chat_stream(messages, tools)         │
│        └─► 边收边 emit TEXT 事件给前端                          │
│        └─► 把 delta.tool_calls[*] 按 index 拼回完整 tool_calls  │
│                                                                │
│   ④ memory.add_assistant_message(                              │
│         content, reasoning_content, tool_calls)                │
│                                                                │
│   ⑤ if resp.tool_calls:                                        │
│        for tc in resp.tool_calls:                              │
│           tool = collection.get(tc.name)                       │
│           result = await ToolExecutor.execute(tool, args)      │
│           memory.add_tool_message(result, tool_call_id=tc.id)  │
│           emit TOOL_RESULT 事件                                 │
│        continue                            ←── 还没收尾，再来一轮│
│                                                                │
│      else (LLM 只回了文字，没要工具):                            │
│        break                               ←── 终止             │
│                                                                │
│ return final_answer                                            │
└───────────────────────────────────────────────────────────────┘
```

**两个细节值得记住：**

1. **Tool calls 全程是 OpenAI 原始字典格式** —— `{id, type, function: {name, arguments}}`，不在中间转成什么 `ToolCall` 数据类。下一轮可以原样塞回 LLM。
2. **Streaming tool_calls 在客户端层就拼好了** —— `OpenAICompatibleClient._collect_stream` 按 index 累积 `arguments` 片段，Handler 永远只看到完整调用。

---

## 5. 工具系统（注册 → 调度 → 执行 → 回写）

```
        ┌─────────── 注册阶段 ───────────┐
        │                                │
@register_tool("search")                 │
class SearchTool(Tool):                  │
    name = "search"                      │
    description = "..."                  │
    schema = {...}                       │
    timeout_seconds = 30      ← 框架会强制超时
    max_result_chars = 4000   ← 自动截断，防爆 Memory
    is_destructive = False    ← 给未来的审批控制留位
    async def run(self, **kw): ...       │
        │                                │
        ▼                                │
   _TOOL_REGISTRY["search"] = SearchTool │
        │                                │
        └────────────────────────────────┘

        ┌──────── 接入 Agent ────────┐
        │                            │
async def setup(self):               │
    tool = create_tool("search")     │  → 工厂从 registry 取出来
    self.context.tool_collection.add(tool)
        │                            │
        └────────────────────────────┘

        ┌──── 给 LLM 看 ────┐
        │                    │
collection.to_openai_tools() │  → [{"type":"function",
        │                              "function":{name, description, parameters}}]
        ▼
       LLM
        │ 决定调用 → tool_calls = [{id, function:{name:"search", arguments:"{...}"}}]
        ▼
        ┌──── ToolExecutor.execute ────┐
        │                              │
        │ ① emit ToolCallStarted        │
        │ ② asyncio.wait_for(           │  ← 强制超时
        │       tool.run(**args),       │
        │       timeout=tool.timeout_seconds)
        │ ③ 成功 → ToolCallCompleted    │
        │    失败 → ToolCallFailed      │  ← 不会让循环挂掉
        │ ④ 大结果 → 按策略截断          │  ← 见 result_summary_strategy
        │                              │
        └──────────────┬───────────────┘
                       ▼
        memory.add_tool_message(result, tool_call_id=...)
                       │
                       ▼
                下一轮 LLM 调用就能看到工具结果
```

---

## 6. 企业中间件洋葱模型

```
┌─────────────────────────────────────────┐
│  TenantIsolationMiddleware              │
│    ┌─────────────────────────────────┐  │
│    │  QuotaMiddleware                │  │
│    │    ┌─────────────────────────┐  │  │
│    │    │  RetryMiddleware        │  │  │
│    │    │    ┌─────────────────┐  │  │  │
│    │    │    │  TracingMiddleware│  │  │  │
│    │    │    │    ┌─────────┐  │  │  │  │
│    │    │    │    │ run_turn│  │  │  │  │
│    │    │    │    └─────────┘  │  │  │  │
│    │    │    └─────────────────┘  │  │  │
│    │    └─────────────────────────┘  │  │
│    └─────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

每个中间件实现一个横切关注点（租户隔离、配额、重试、追踪），通过 `MiddlewareChain` 组合。外层先执行前置逻辑，内层先执行后置逻辑。

---

## 7. 事件双轨架构

```
run_turn() 内部
    │
    ├─► emit(RuntimeEvent) ─────────────────────┐
    │                                            │
    │    ┌─────────────────┐    ┌────────────┐  │
    └────┤ RuntimeEventFanout│    │ JsonlSink  │  │
         └────────┬────────┘    └────────────┘  │
                  │                               │
                  ├─► on_event callback (给 Service)
                  │       │
                  │       ▼
                  │   ┌─────────────┐
                  │   │  SseSink    │
                  │   │  Printer    │
                  │   └──────┬──────┘
                  │          │
                  │          ▼
                  │      SSE v2 流
                  │      (给前端)
                  │
                  └─► logs/runs/2024-01-15/run_xxx.jsonl
```

---

## 8. 设计取舍（给想扩展的人）

| 决定 | 为什么这么做 | 如果不这么做 |
|------|------------|-----------|
| Loop 在 runtime 里，不在 Agent 里 | 让 Agent 保持声明式，循环策略集中维护 | 每个 Agent 重写循环，逻辑漂移 |
| Tool calls 走 OpenAI 原始 dict | 直接塞回 LLM，免转换、免漂移 | 需要往返转换层，容易出 bug |
| Streaming 在 client 层就拼好 | Handler 永远只看到完整 tool_calls，代码简单 | Handler 里要处理片段状态机 |
| `Printer` 信封字段写死 | 前端契约稳定 ↔ 内部模型可演进 | 改内部事件会打破前端 |
| RuntimeEvent 与 EventType 分两层 | 加内部诊断不会影响前端协议 | 前端被迫适配内部变化 |
| Tool 自带 `timeout` / `max_result_chars` / `is_destructive` | 工具治理放在工具自己身上，不是散落各处 | 超时/截断逻辑散落在 executor 和各处 |
| frozen AgentSpec + 显式 REGISTRY | 配置不可变，注册一目了然 | 隐式注册导致依赖混乱 |
| Hook 返回 `HookResult` 而非抛异常控制流 | 明确表达 "继续 / 软失败 / 中止" | 异常控制流难以追踪 |
| Memory 双限制（消息数 + Token 数） | 兼容不同场景：有的按条数，有的按长度 | 单一限制无法覆盖所有场景 |

---

## 9. 状态机：AgentState

```
        ┌─────────┐
        │  IDLE   │ ◄──── 初始状态
        └────┬────┘
             │ setup()
             ▼
        ┌─────────┐
   ┌───►│ RUNNING │◄──── 循环执行中
   │     └────┬────┘
   │          │
   │    ┌─────┴─────┐
   │    ▼           ▼
   │ ┌───────┐   ┌─────────┐
   │ │FINISHED│   │  ERROR  │
   │ └───┬───┘   └────┬────┘
   │     │            │
   │     │ teardown()  │
   │     ▼            ▼
   │  ┌─────────────────┐
   └──┤   (结束运行)    │
      └─────────────────┘

   CancelledError ──► CANCELLED
```

---

## 10. 扩展点速查

| 我想做... | 去哪里扩展 |
|-----------|-----------|
| 加一个新 Agent | `agents/<name>/spec.py` + `agents/__init__.py` 注册 |
| 加一个新 Tool | 继承 `Tool` → `@register_tool` → Agent 的 setup 里 add |
| 换 LLM 提供商 | 实现 `LLMClient` 协议，或改 `LLM_BASE_URL` |
| 接自己的前端 | 订阅 `event_stream`，按 `event:` 名称分发 |
| 加运行前检查 | `HookManager` 注册 `SESSION_START` / `USER_PROMPT_SUBMIT` |
| 加工具调用拦截 | `HookManager` 注册 `PRE_TOOL_USE` / `POST_TOOL_USE` |
| 加企业管控 | 写 `MiddlewareFn` 接入 `MiddlewareChain` |
| 持久化对话 | 实现 `PersistencePort` 注入 `AgentContext` |
| 看一次运行全貌 | 打开 `logs/runs/<日期>/<run_id>.jsonl` |

---

> 本文档以 `src/agentengine/` 当前实现为准。如果代码有改动，这份文档也应该跟着更新。
