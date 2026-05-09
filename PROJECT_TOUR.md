# AgentKit Refactor — 一篇看懂

> 一份图文并茂的项目拆解。看完这一篇,你应该能回答:**这个项目在做什么、由哪些零件组成、一条用户请求如何穿过它、我该从哪里下手扩展。**

---

## 1. 一句话定位

> 把 Agent 的「**循环逻辑**」「**LLM 客户端**」「**工具调用**」「**记忆**」「**事件流**」全部解耦成可独立演进的模块,业务 Agent 只声明 **"我用什么提示词、注册什么工具"**,框架负责把它跑起来。

它是一个**框架级的脚手架**,不是一个具体应用。三个落地形态:

| 角色 | 例子 | 长什么样 |
|---|---|---|
| 普通对话 Agent | `general_chat` | 没工具,纯聊天 |
| 多步研究 Agent | `deep_research` | 不挂显式规划工具,靠模型自己拆步骤 + 调工具 |
| 老代码适配器 | `file_clerk` | 用 `LegacyHandler` 包住旧实现,慢慢迁移 |

---

## 2. 整体分层(一张图看全)

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
│      生成 run_id/turn_id,记录生命周期事件 → JSONL 日志                    │
└─────────────────────────────────────────────────────────────────────────┘
                                        │ handler.handle(agent, ctx, query)
                                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  ② runtime/turn.py  run_turn() — 唯一的 think→act 循环                  │
│      Phase 3 已将原 ReActHandler 内联为函数,handlers/ 目录已删除          │
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
  tools/registry @register_tool 装饰器（Agent / Handler 已改为显式注册或函数）
  prompts/       PromptLoader  (读 YAML 提示词,带缓存)
  skills/        SkillLoader   (扫 .agent/skills/SKILL.md)
  observability/ RunEventLog   (logs/runs/<日期>/<run_id>.jsonl)
  errors.py      AgentKitError 家族 (LLMError / ToolExecutionError …)
```

**记忆口诀:** `services → runtime → handlers → (base · llm · memory · tools) → stream`,左到右就是一条请求的旅行路线。

---

## 3. 一条请求的完整旅行(时序图)

下图就是 `run_agent.py` 调一次 `general_chat` 时,各模块之间发生的事:

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
 │                   │                   │                   │                   │                   │     START 事件   │
 │                   │                   │                   ├──────────────────────────────────────────────────────────►│
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

**两个关键不要混:**
- 走 **EventStream/Printer** 的 → 给前端看的(SSE 信封,字段固定)。
- 走 **TurnRunner.on_event()** 的 → 给后端记录的(RuntimeEvent,内部诊断)。
- 它们刻意分层:前端契约不会因为内部加新事件而被打破。

---

## 4. 核心抽象:AgentSpec + AgentRun(当前架构)

**AgentSpec** 是 `frozen=True` 的 dataclass——声明"这个 Agent 是什么"（提示词、步数上限、setup/teardown 钩子）。它与运行无关，可跨请求共享。

**AgentRun** 是可变的 per-run 容器——持有 Memory、当前步数、AgentState。每次请求创建一个新实例。

```
┌──────────────────────────────────┐        ┌──────────────────────────────────┐
│         AgentSpec  (不可变配置)   │        │       AgentRun  (运行状态)        │
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

写一个新 Agent 只需要:

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

`max_steps` 等参数现在直接在 `AgentSpec` 中声明；单次运行仍可通过 `agent_kwargs` 覆盖。

---

## 5. ReAct 循环细节(代码视角)

`run_turn()._loop` 在 [src/agentkit/runtime/turn.py](src/agentkit/runtime/turn.py) 里:

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
│        continue                            ←── 还没收尾,再来一轮│
│                                                                │
│      else (LLM 只回了文字,没要工具):                            │
│        break                               ←── 终止             │
│                                                                │
│ return final_answer                                            │
└───────────────────────────────────────────────────────────────┘
```

**两个细节值得记住:**

1. **Tool calls 全程是 OpenAI 原始字典格式** —— `{id, type, function: {name, arguments}}`,不在中间转成什么 `ToolCall` 数据类。下一轮可以原样塞回 LLM。
2. **Streaming tool_calls 在客户端层就拼好了** —— `OpenAICompatibleClient._collect_stream` 按 index 累积 `arguments` 片段,Handler 永远只看到完整调用。

---

## 6. 工具系统(注册 → 调度 → 执行 → 回写)

```
        ┌─────────── 注册阶段 ───────────┐
        │                                │
@register_tool("search")                 │
class SearchTool(Tool):                  │
    name = "search"                      │
    description = "..."                  │
    schema = {...}                       │
    timeout_seconds = 30      ← 框架会强制超时
    max_result_chars = 4000   ← 自动截断,防爆 Memory
    is_destructive = False    ← 给未来的人审控制留位
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

**内置工具:**

| 工具 | 作用 |
|---|---|
| `SkillTool` | 自动注入 Claude Code 风格的 skills(扫 `.agent/skills/`),让 Agent 拿来即用 |

> Deep Research 采用模型原生规划：模型先拆解任务，再通过 `next_step_prompt()` 引导逐步执行。执行过程会被 `step`、`thinking`、`tool_call_start`、`tool_result`、`step_end`、`usage`、`result` 这些事件展示出来。

---

## 7. 流式协议(给前端的契约)

> 完整规范见 [docs/STREAMING_PROTOCOL.md](docs/STREAMING_PROTOCOL.md)。

每条事件被 `Printer` 包成同一个 SSE 信封:

```
┌────────────────────── SSE 信封 (Printer.send) ──────────────────────┐
│ {                                                                    │
│   "responseType":    <事件类型,见下表>                                │
│   "response":        <主体文本(给老前端看)>                           │
│   "responseAll":     ""                                              │
│   "useTimes":        0,                                              │
│   "reqId":           "<request_id>",                                 │
│   "errorMsg":        null | "...",                                   │
│   "resultMap":       null | {...}    ← 结构化数据,新前端读这个       │
│   "conversation_id": "<conv_id>",                                    │
│   "finished":        false | true                                    │
│ }                                                                    │
└──────────────────────────────────────────────────────────────────────┘
```

**事件类型表:**

| responseType        | 时机                | resultMap 关键字段                                   | finished |
|---------------------|---------------------|----------------------------------------------------|----------|
| `start`             | run 启动            | —                                                  | false    |
| `step`              | 第 N 轮开始         | `turn`                                             | false    |
| `thinking`          | 模型 CoT 增量        | —(`response` 是 delta 字符串)                       | false    |
| `text`              | 最终答案增量          | —(`response` 是 delta 字符串)                       | false    |
| `tool_call_start`   | 工具开始执行          | `tool / arguments / tool_call_id`                  | false    |
| `tool_result`       | 工具完成             | `tool / toolResult / ok / elapsed_seconds / tool_call_id / [error_type]` | false |
| `step_end`          | 第 N 轮结束          | `turn / has_tool_calls / elapsed_seconds`          | false    |
| `usage`             | run 结束前          | `prompt_tokens / completion_tokens / total_tokens / total_seconds` | false    |
| `result`            | **终态:成功**       | `taskSummary / result`                             | **true** |
| `error`             | **终态:失败**       | `code / message / category / retryable / [details]` | **true** |

**典型事件序列(单轮无工具):**
```
start ──▶ step ──▶ thinking ... ──▶ text ... ──▶ step_end ──▶ usage ──▶ result
```

**典型事件序列(两轮带工具):**
```
start ──▶ step ──▶ thinking ──▶ tool_call_start ──▶ tool_result ──▶ step_end
                                                                          │
                                ▼─── (loop) ────────────────────────────┘
                                step ──▶ text ──▶ step_end ──▶ usage ──▶ result
```

**事件双轨并行(刻意分层):**

| 通道                       | 类型             | 给谁看           | 例子                                                  |
|----------------------------|-----------------|------------------|------------------------------------------------------|
| `EventStream`(前端 SSE)   | `EventType`      | 终端用户 / Web   | `text` / `tool_result` / `result` / `error`           |
| `RuntimeEvent`(内部)       | `RuntimeEvent` 子类 | 后端日志 / 监控 | `RunStarted` / `ToolCallStarted` / `UsageReport` / `RunCompleted` |

`Printer.from_runtime_event()` 提供两层之间的标准翻译。前端契约稳定 ↔ 内部模型可演进。

`Printer.from_runtime_event()` 提供两层之间的标准翻译,你可以选择用或不用。

---

## 8. 显式 Agent 注册

`src/agents/__init__.py` 是 Agent 注册入口。默认参数直接写在各自的
`src/agents/*/spec.py` 中，运行时通过 `agents.REGISTRY` 查找。

**Service 启动时:**

```
service = AgentOrchestrationService()
                          │
                          ▼
        ┌────── 调用 service.run(name) ──────┐
        │                                    │
        │ ① spec = AGENT_REGISTRY[name]      │
        │ ② 合并 agent_kwargs 里的 max_steps  │
        │   → replace(spec, max_steps=N)     │  ← frozen spec 安全
        │ ③ agent = AgentRun(spec, context)  │
        │ ④ TurnRunner.run(agent)            │
        └────────────────────────────────────┘
```

---

## 9. 三个 Agent 的画像

```
┌────────────────────────────────────────────────────────────────────┐
│ general_chat                                                        │
│ ─────────────────────────────────────────────────────────────────── │
│ 用途   : 普通问答                                                    │
│ 循环   : run_turn()                                                  │
│ 工具   : 无 (但 SkillTool 由 AgentContext 自动挂上)                  │
│ 特点   : 没工具调用 → 一轮就 break;最简单的 agent 模板               │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ deep_research                                                       │
│ ─────────────────────────────────────────────────────────────────── │
│ 用途   : 多步研究                                                    │
│ 循环   : run_turn()                                                  │
│ 工具   : 通过 SkillLoader 按名字加载;不挂显式 planning 工具          │
│ 特点   : system_prompt 引导模型自己拆解任务、逐步执行、合成报告       │
│         next_step_prompt 每轮提醒"推进下一个子任务"                  │
│         max_steps = 10,留足思考空间                                 │
└────────────────────────────────────────────────────────────────────┘

```

> `file_clerk` 适配器（`LegacyHandler` + `PipelineHandler`）已在 Phase 1 删除。

---

## 10. 五分钟跑起来

最短路径在 [run_agent.py](run_agent.py):

```python
# 1) 准备环境变量(.env)
LLM_API_KEY=sk-...
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1   # 可选,默认就是这个

# 2) 创建 LLM
llm = create_llm_from_env(required=True)

# 3) 起服务
service = AgentOrchestrationService()

# 4) 拿到一个流式 Context(reqId / convId / EventStream 都齐了)
context, event_stream = service.create_streaming_context(
    request_id="quick-test-001",
    query="...",
    conversation_id="test-conv-001",
)
context.llm = llm

# 5) 跑 Agent
result = await service.run(
    agent_name="general_chat",
    query="请用中文简要介绍一下这个项目",
    context=context,
)
```

把 `agent_name` 换成 `deep_research`,LLM 会自己先在思维链里拆好计划,再一步步答。

---

## 11. 目录速查表

```
src/
├─ agentkit/                     ← 框架代码,不依赖任何业务
│  ├─ spec.py      AgentSpec(frozen dataclass) ← NEW
│  ├─ base/        AgentRun · AgentContext · AgentState
│  ├─ runtime/     turn.py(run_turn 唯一循环) · turn_runner · run_state · events
│  ├─ llm/         OpenAICompatibleClient · 工厂 · 协议
│  ├─ memory/      Message · Memory(自动裁剪)
│  ├─ tools/       Tool · Collection · Registry · Executor + builtin/
│  ├─ stream/      EventStream · Printer · EventType
│  ├─ prompts/     YAML 提示词加载器(带缓存)
│  ├─ skills/      Claude Code 风格 SKILL.md 扫描
│  ├─ observability/ JSONL 运行日志
│  └─ errors.py    AgentKitError 家族(可序列化、可重试标记)
│
├─ agents/                         ← Agent 规格声明
│  ├─ __init__.py  REGISTRY: dict[str, AgentSpec] ← NEW
│  ├─ general_chat/spec.py
│  └─ deep_research/spec.py
│
└─ services/
   └─ agent_orchestration_service.py  ← 应用入口

scripts/
├─ chat.py               ← 简单 CLI(打 raw 事件)
├─ chat_pretty.py        ← Claude Code 风格 CLI(Rich 卡片)
└─ renderers/
   ├─ rich_renderer.py   ← 终端渲染器
   └─ friendly_errors.py ← 错误码 → 用户语

web/
├─ src/App.tsx           ← React 应用主入口
├─ src/types.ts          ← SseEvent / RunTrace / ErrorPayload
├─ src/traceReducer.ts   ← SSE → UI 状态归约器
├─ src/traceTransport.ts ← fetch + SSE 解析
└─ src/friendlyErrors.ts ← 错误码 → 用户语(与 Python 镜像)

tests/                  ← 完整 pytest 套件(含 SSE 黄金兼容测试)
docs/API.md             ← 公共契约文档
docs/STREAMING_PROTOCOL.md ← SSE 协议规范(本文档的扩写版)
run_agent.py            ← 五分钟体验脚本
```

---

## 12. 设计取舍(给想扩展的人)

| 决定 | 为什么这么做 |
|---|---|
| Loop 在 runtime 里,不在 Agent 里 | 让 Agent 保持声明式,循环策略集中维护 |
| Tool calls 走 OpenAI 原始 dict | 直接塞回 LLM,免转换、免漂移 |
| Streaming 在 client 层就拼好 | Handler 永远只看到完整 tool_calls,代码简单 |
| `Printer` 信封字段写死 | 前端契约稳定 ↔ 内部模型可演进 |
| RuntimeEvent 与 EventType 分两层 | 加内部诊断不会影响前端协议 |
| `USE_LEGACY_RUNNER=true` 后门 | 灰度切换 / 故障兜底 |
| `LegacyHandler` + 适配器 | 不重写老代码,先包装再迁移 |
| Tool 自带 `timeout` / `max_result_chars` / `is_destructive` | 工具治理放在工具自己身上,不是散落各处 |

---

## 13. 产品形态:终端 + Web 双前端

同一份 SSE 事件流,被两个独立的渲染器消费:

```
                   AgentOrchestrationService.run()
                              │
                              ▼  (SSE 事件流)
                       EventStream
                       /         \
                      /           \
                     ▼             ▼
        ┌────────────────┐    ┌─────────────────┐
        │ scripts/       │    │ web/            │
        │ chat_pretty.py │    │ React + Vite    │
        │  Rich 卡片      │    │  折叠思考 + 工具卡 │
        │ Claude Code 风格 │    │  Kimi 风格       │
        └────────────────┘    └─────────────────┘
```

**终端 — Claude Code 风格静态卡片:**

```
─── 🤖 deep_research  调研 src/agentkit 的整体结构 ───

  ▸ Turn 1
    💭 Thinking (21 chars hidden — pass --show-reasoning expanded to view)
┌─ 🔧 read_file ─────────────────────┐
│ { "path": "README.md" }           │
└────────────────────────────────────┘
┌─ ✓ read_file · Result  (0.04s) ───┐
│ # AgentKit Refactor             │
└────────────────────────────────────┘

  ▸ Turn 2
┌─ 📝 Answer ────────────────────────┐
│ 这个项目是一个 AgentKit 框架...   │
└────────────────────────────────────┘
┌──────── ✓ Done ────────────────────┐
│         Total tokens  351          │
│             Duration  2.00s        │
└────────────────────────────────────┘
```

跑法:
```bash
PYTHONIOENCODING=utf-8 uv run python scripts/chat_pretty.py general_chat "你好"
PYTHONIOENCODING=utf-8 uv run python scripts/chat_pretty.py deep_research "..." --show-reasoning expanded
```

**Web — React + FastAPI:**

```
后端:  uv run uvicorn services.web_api:app --port 8000
前端:  cd web && npm run dev          # http://localhost:5173
```

后端的 `/api/runs/stream?query=...&agent_name=...` 直接 SSE 出 `Printer` 信封,前端 [traceReducer.ts](web/src/traceReducer.ts) 把流量归约成 `RunTrace`,UI 按 step / tool / final / usage 分卡片渲染。

**两端共享一份「错误码 → 用户友好语」表:**
- Python: [scripts/renderers/friendly_errors.py](scripts/renderers/friendly_errors.py)
- TypeScript: [web/src/friendlyErrors.ts](web/src/friendlyErrors.ts)

加新错误码时**必须**两边一起改,文档在 [docs/STREAMING_PROTOCOL.md](docs/STREAMING_PROTOCOL.md) 的标准码表。

---

## 14. 你接下来可能想做什么

- **加一个新 Agent**:复制 `general_chat`,改 system prompt 和 setup,在 `agents.REGISTRY` 里登记。
- **加一个新工具**:继承 `Tool`,加 `@register_tool("xxx")`,在 Agent 的 setup 里 add。
- **换 LLM 提供商**:实现 `LLMClient` 协议(`chat` + `chat_stream`),或调 `LLM_BASE_URL` 指向兼容 OpenAI 的端点。
- **接你自己的前端**:订阅 `event_stream`,按 `responseType` 分发即可,字段已经稳定。
- **看一次运行的全貌**:打开 `logs/runs/<日期>/<run_id>.jsonl`,每行一个 RuntimeEvent。

---

> 本文是从源码反推出来的,以 `src/agentkit/` 当前实现为准。如果代码有改动,这份文档也应该跟着更新。
