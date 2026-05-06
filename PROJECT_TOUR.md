# Agent Core Refactor — 一篇看懂

> 一份图文并茂的项目拆解。看完这一篇,你应该能回答:**这个项目在做什么、由哪些零件组成、一条用户请求如何穿过它、我该从哪里下手扩展。**

---

## 1. 一句话定位

> 把 Agent 的「**循环逻辑**」「**LLM 客户端**」「**工具调用**」「**记忆**」「**事件流**」全部解耦成可独立演进的模块,业务 Agent 只声明 **"我用什么提示词、注册什么工具"**,框架负责把它跑起来。

它是一个**框架级的脚手架**,不是一个具体应用。三个落地形态:

| 角色 | 例子 | 长什么样 |
|---|---|---|
| 普通对话 Agent | `general_chat` | 没工具,纯聊天 |
| 多步研究 Agent | `deep_research` | 注册 `PlanningTool`,边规划边调工具 |
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
│      读 agents.yaml → 找 Agent → 找 Handler → 交给 TurnRunner            │
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
│  ② handlers/   ReActHandler · PipelineHandler · LegacyHandler           │
│      "循环策略" 在这里。think→act 的真正实现                              │
└─────────────────────────────────────────────────────────────────────────┘
                          │            │            │
                          ▼            ▼            ▼
┌───────────────────┐ ┌──────────┐ ┌───────────┐ ┌────────────────────┐
│ ③ base/           │ │ ④ llm/   │ │ ⑤ memory/ │ │ ⑥ tools/           │
│ BaseAgent         │ │ OpenAI-  │ │ Message · │ │ Tool · Collection ·│
│ AgentContext      │ │ Compat   │ │ Memory    │ │ Registry · Executor│
│ AgentState        │ │ Client   │ │ (自动裁剪) │ │ + PlanningTool等   │
└───────────────────┘ └──────────┘ └───────────┘ └────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  ⑦ stream/     EventStream · Printer · EventType                        │
│      把内部事件 → 前端 SSE 信封 (responseType / response / finished …)    │
└─────────────────────────────────────────────────────────────────────────┘

辅助层:
  registry/      @register_agent / @register_handler / @register_tool 装饰器
  prompts/       PromptLoader  (读 YAML 提示词,带缓存)
  skills/        SkillLoader   (扫 .agent/skills/SKILL.md)
  observability/ RunEventLog   (logs/runs/<日期>/<run_id>.jsonl)
  errors.py      AgentCoreError 家族 (LLMError / ToolExecutionError …)
```

**记忆口诀:** `services → runtime → handlers → (base · llm · memory · tools) → stream`,左到右就是一条请求的旅行路线。

---

## 3. 一条请求的完整旅行(时序图)

下图就是 `run_agent.py` 调一次 `general_chat` 时,各模块之间发生的事:

```
用户              Service             TurnRunner          ReActHandler        LLM Client          ToolExecutor        Printer
 │                   │                   │                   │                   │                   │                  │
 │  run("general_chat", query)           │                   │                   │                   │                  │
 ├──────────────────►│                   │                   │                   │                   │                  │
 │                   │ create_agent()                        │                   │                   │                  │
 │                   │ create_handler()                      │                   │                   │                  │
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

## 4. 核心抽象:Agent vs Handler(本项目最关键的设计)

很多 Agent 框架把"循环逻辑"塞进 Agent 类,导致每写一个新 Agent 就要继承一坨基类,改循环还要改基类。

**本项目反过来:**

```
┌──────────────────────────────────┐        ┌──────────────────────────────────┐
│         BaseAgent  (容器)         │        │       Handler  (策略)            │
│                                  │        │                                  │
│  • memory                        │        │  • ReActHandler   think→act      │
│  • state                         │  ←──→  │  • PipelineHandler 固定步骤      │
│  • current_step / max_steps      │        │  • LegacyHandler   旧代码桥      │
│  • setup()      注册工具/提示词   │        │                                  │
│  • teardown()   清理              │        │  handle(agent, context, query)   │
│  • system_prompt()                │        │    → 控制怎么循环、怎么调 LLM    │
│  • next_step_prompt()             │        │    → 控制怎么调度工具            │
└──────────────────────────────────┘        └──────────────────────────────────┘
        声明性的"我是谁"                           过程性的"怎么跑"
```

写一个新 Agent 只需要:

```python
@register_agent("my_agent", handler="react")
class MyAgent(BaseAgent):
    def system_prompt(self) -> str:
        return "你是一个 ..."

    async def setup(self) -> None:
        self.context.tool_collection.add(MySearchTool())
```

**循环策略**则在 `agents.yaml` 里随便切:

```yaml
agents:
  my_agent:
    handler: react        # 也可以改成 pipeline / legacy
    max_steps: 10
```

---

## 5. ReAct 循环细节(代码视角)

`ReActHandler._loop` 在 [src/agent_core/handlers/react.py](src/agent_core/handlers/react.py) 里:

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

**两个内置工具:**

| 工具 | 作用 |
|---|---|
| `PlanningTool` | 让 LLM 写"步骤清单",把多步任务显式化(Deep Research 用它) |
| `SkillTool` | 自动注入 Claude Code 风格的 skills(扫 `.agent/skills/`),让 Agent 拿来即用 |

---

## 7. 流式协议(给前端的契约)

每条事件被 `Printer` 包成同一个信封:

```
┌────────────────────── SSE 信封 (Printer.send) ──────────────────────┐
│ {                                                                    │
│   "responseType":   "start" | "text" | "task" | "tool_thought"      │
│                   | "tool_result" | "result" | "error" | "done"     │
│   "response":       <主体数据>                                       │
│   "responseAll":    <主体数据(冗余,兼容旧前端)>                    │
│   "useTimes":       0,                                              │
│   "reqId":          "<request_id>",                                 │
│   "errorMsg":       null | "...",                                   │
│   "resultMap":      null | {...},                                   │
│   "conversation_id":"<conv_id>",                                    │
│   "finished":       false | true     ← true = 整次会话结束          │
│ }                                                                    │
└──────────────────────────────────────────────────────────────────────┘
```

**前端事件序列(典型):**

```
START ──► TEXT TEXT TEXT ... ──► TOOL_RESULT ──► TEXT TEXT ... ──► RESULT(finished=true)
   │                                                                      │
   │             多次 TEXT 是 LLM 流式打字效果                             │
   │             TOOL_RESULT 是工具调用结果,夹杂在 TEXT 之间               │
                                                                          ▼
                                                        前端拼装最终回复 + 关闭 SSE
```

**事件双轨并行:**

| 通道 | 类型 | 给谁看 | 例子 |
|---|---|---|---|
| `EventStream` (前端 SSE) | `EventType` | 终端用户 | `TEXT`, `TOOL_RESULT`, `RESULT`, `ERROR` |
| `RuntimeEvent` (内部) | `RuntimeEvent` 子类 | 后端日志、监控 | `RunStarted`, `ToolCallStarted`, `RunCompleted` |

`Printer.from_runtime_event()` 提供两层之间的标准翻译,你可以选择用或不用。

---

## 8. 配置驱动:agents.yaml 的作用

[config/agents.yaml](config/agents.yaml) 是「**运行期开关 + 默认参数**」:

```yaml
agents:
  general_chat:
    enabled: true          # ← 一行关闭这个 agent
    handler: react         # ← 改成 pipeline 它就走另一种循环
    default_model: default
    max_steps: 3           # ← 不改代码就能调上限

  deep_research:
    enabled: true
    handler: react
    max_steps: 10

  file_clerk:
    enabled: true
    handler: pipeline
    legacy_adapter: true   # ← 标记是旧代码适配器

compatibility:
  legacy_agent_type_map:
    "3": deep_research     # ← 老系统传"agentType=3"时,路由到 deep_research
```

**Service 启动时:**

```
service = AgentOrchestrationService(config_path="config/agents.yaml")
                          │
                          ▼
        ┌────── 调用 service.run(name) ──────┐
        │                                    │
        │ ① is_enabled(name)?  否 → 报错      │
        │ ② 合并 config 里的 max_steps 等参数  │
        │ ③ handler = cfg.handler            │
        │            ?? get_agent_handler(name) (装饰器登记的默认)
        │ ④ create_agent(name, ctx, **kw)    │
        │ ⑤ create_handler(handler, **kw)    │
        │ ⑥ 走 TurnRunner / 或 LegacyHandler │
        │   (USE_LEGACY_RUNNER=true 切换)    │
        └────────────────────────────────────┘
```

---

## 9. 三个 Agent 的画像

```
┌────────────────────────────────────────────────────────────────────┐
│ general_chat                                                        │
│ ─────────────────────────────────────────────────────────────────── │
│ 用途   : 普通问答                                                    │
│ Handler: ReActHandler                                                │
│ 工具   : 无 (但 SkillTool 由 AgentContext 自动挂上)                  │
│ 特点   : 没工具调用 → 一轮就 break;最简单的 agent 模板               │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ deep_research                                                       │
│ ─────────────────────────────────────────────────────────────────── │
│ 用途   : 多步研究                                                    │
│ Handler: ReActHandler                                                │
│ 工具   : PlanningTool (在 setup() 里 add)                           │
│ 特点   : system_prompt 强制 LLM 先做计划                             │
│         next_step_prompt 每轮提醒"推进到下一步"                      │
│         max_steps = 10,留足规划空间                                 │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ file_clerk  (适配器)                                                 │
│ ─────────────────────────────────────────────────────────────────── │
│ 用途   : 复用老的 FileClerk 实现                                     │
│ Handler: PipelineHandler                                             │
│ 关键   : legacy_factory 注入老对象,把它当成 pipeline 的一个 step     │
│         旧代码内部的 queue 消息桥接到新的 Printer 上                 │
│ 意义   : 演示"先包装,再迁移"的渐进路线                              │
└────────────────────────────────────────────────────────────────────┘
```

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
service = AgentOrchestrationService(config_path="config/agents.yaml")

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

把 `agent_name` 换成 `deep_research`,LLM 会先调 `PlanningTool` 写个计划再答。

---

## 11. 目录速查表

```
src/
├─ agent_core/                     ← 框架代码,不依赖任何业务
│  ├─ base/        BaseAgent · AgentContext · AgentState
│  ├─ handlers/    react · pipeline · legacy
│  ├─ llm/         OpenAICompatibleClient · 工厂 · 协议
│  ├─ memory/      Message · Memory(自动裁剪)
│  ├─ tools/       Tool · Collection · Registry · Executor + builtin/
│  ├─ stream/      EventStream · Printer · EventType
│  ├─ runtime/     TurnRunner · RunState · RuntimeEvent
│  ├─ registry/    @register_agent / @register_handler
│  ├─ prompts/     YAML 提示词加载器(带缓存)
│  ├─ skills/      Claude Code 风格 SKILL.md 扫描
│  ├─ observability/ JSONL 运行日志
│  └─ errors.py    AgentCoreError 家族(可序列化、可重试标记)
│
├─ agents/                         ← 具体 Agent 声明
│  ├─ general_chat/
│  ├─ deep_research/
│  └─ adapters/file_clerk_adapter.py
│
└─ services/
   └─ agent_orchestration_service.py  ← 应用入口

config/agents.yaml      ← 开关 + 默认参数
tests/                  ← 完整 pytest 套件(含 SSE 黄金兼容测试)
docs/API.md             ← 公共契约文档
run_agent.py            ← 五分钟体验脚本
```

---

## 12. 设计取舍(给想扩展的人)

| 决定 | 为什么这么做 |
|---|---|
| Loop 在 Handler 里,不在 Agent 里 | 让 Agent 保持声明式,循环策略可以通过 yaml 切换 |
| Tool calls 走 OpenAI 原始 dict | 直接塞回 LLM,免转换、免漂移 |
| Streaming 在 client 层就拼好 | Handler 永远只看到完整 tool_calls,代码简单 |
| `Printer` 信封字段写死 | 前端契约稳定 ↔ 内部模型可演进 |
| RuntimeEvent 与 EventType 分两层 | 加内部诊断不会影响前端协议 |
| `USE_LEGACY_RUNNER=true` 后门 | 灰度切换 / 故障兜底 |
| `LegacyHandler` + 适配器 | 不重写老代码,先包装再迁移 |
| Tool 自带 `timeout` / `max_result_chars` / `is_destructive` | 工具治理放在工具自己身上,不是散落各处 |

---

## 13. 你接下来可能想做什么

- **加一个新 Agent**:复制 `general_chat`,改 system prompt 和 setup,在 yaml 里登记。
- **加一个新工具**:继承 `Tool`,加 `@register_tool("xxx")`,在 Agent 的 setup 里 add。
- **换 LLM 提供商**:实现 `LLMClient` 协议(`chat` + `chat_stream`),或调 `LLM_BASE_URL` 指向兼容 OpenAI 的端点。
- **接你自己的前端**:订阅 `event_stream`,按 `responseType` 分发即可,字段已经稳定。
- **看一次运行的全貌**:打开 `logs/runs/<日期>/<run_id>.jsonl`,每行一个 RuntimeEvent。

---

> 本文是从源码反推出来的,以 `src/agent_core/` 当前实现为准。如果代码有改动,这份文档也应该跟着更新。
