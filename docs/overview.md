# AgentEngine 总体介绍

> 一份全面的项目说明书：AgentEngine 是什么、解决什么问题、由哪些零件组成、能做什么、长什么样。

---

## 一句话定位

AgentEngine 是一个**声明式 Agent 执行框架**。它将「循环逻辑」「LLM 客户端」「工具调用」「记忆管理」「事件流」全部解耦成可独立演进的模块，业务 Agent 只负责声明 **"我用什么提示词、注册什么工具"**，框架负责把它跑起来。

它不是某个具体应用，而是一个**框架级脚手架**。你可以用它搭建：

| 形态 | 示例 | 特征 |
|------|------|------|
| 普通对话 Agent | `general_chat` | 无工具，纯聊天 |
| 多步研究 Agent | `deep_research` | 模型自己拆解任务 + 逐步调工具 |
| 企业级服务 | Web API + 租户隔离 | 审批门、配额、链路追踪 |

---

## 为什么要做 AgentEngine

在传统的 Agent 实现中，一个 Agent 往往是一个巨大的类：

```python
class MyAgent(BaseAgent):
    async def run(self, query):
        # 循环逻辑、LLM 调用、工具执行、记忆管理、事件发送...
        # 全部写在一个类里
```

这带来几个问题：

1. **循环逻辑与业务耦合** —— 换个 Agent 要重写循环
2. **工具治理散落各处** —— 超时、截断、审批没有统一入口
3. **前端协议与内部实现纠缠** —— 加一个新诊断事件可能破坏前端
4. **测试困难** —— 想测工具执行必须拉起整个 Agent

AgentEngine 的解耦思路：

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  AgentSpec  │     │  run_turn() │     │   Printer   │
│  (声明我是谁) │ ──► │ (统一的循环) │ ──► │ (前端契约)  │
└─────────────┘     └─────────────┘     └─────────────┘
```

---

## 核心能力矩阵

| 能力 | 说明 | 相关模块 |
|------|------|---------|
| **声明式 Agent** | 用 frozen dataclass 描述 Agent，而非继承类 | `base/`, `spec.py` |
| **统一 ReAct 循环** | 唯一的 `run_turn()` 函数驱动所有 Agent | `runtime/turn.py` |
| **OpenAI 兼容** | 原生支持 OpenAI 格式 tool_calls，端到端透传 | `llm/`, `memory/` |
| **流式输出** | 边收 LLM chunk 边 emit 事件，零延迟感知 | `stream/`, `runtime/` |
| **工具治理** | 统一超时、截断、审批、配额、破坏性检测 | `tools/`, `enterprise/` |
| **双轨事件** | 内部 RuntimeEvent ↔ 公共 SSE 刻意分层 | `runtime/events.py`, `stream/` |
| **记忆裁剪** | 按消息数 / Token 数自动裁剪，保留系统消息 | `memory/` |
| **可观测性** | 每轮运行生成 JSONL 日志，可追溯复现 | `observability/` |
| **企业中间件** | 洋葱式中间件链：租户隔离 → 配额 → 重试 → 追踪 | `enterprise/` |
| **生命周期钩子** | PreToolUse / PostToolUse / SessionStart 等拦截点 | `hooks/` |

---

## 产品形态：终端 + Web 双前端

同一份 SSE 事件流，被两个独立渲染器消费：

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

### 终端 — Claude Code 风格静态卡片

```
─── 🤖 deep_research  调研 src/agentengine 的整体结构 ───

  ▸ Turn 1
    💭 Thinking (21 chars hidden — pass --show-reasoning expanded to view)
┌─ 🔧 read_file ─────────────────────┐
│ { "path": "README.md" }           │
└────────────────────────────────────┘
┌─ ✓ read_file · Result  (0.04s) ───┐
│ # AgentEngine Refactor             │
└────────────────────────────────────┘

  ▸ Turn 2
┌─ 📝 Answer ────────────────────────┐
│ 这个项目是一个 AgentEngine 框架...   │
└────────────────────────────────────┘
┌──────── ✓ Done ────────────────────┐
│         Total tokens  351          │
│             Duration  2.00s        │
└────────────────────────────────────┘
```

跑法：
```bash
PYTHONIOENCODING=utf-8 uv run python scripts/chat_pretty.py general_chat "你好"
PYTHONIOENCODING=utf-8 uv run python scripts/chat_pretty.py deep_research "..." --show-reasoning expanded
```

### Web — React + FastAPI

```bash
后端:  uv run --env-file .env uvicorn examples.reference_app.services.web_api:app --host 127.0.0.1 --port 8000
前端:  cd web && npm run dev          # http://localhost:5173
```

后端的 `/api/runs/stream` 直接 SSE 出 `Printer` 信封，前端 `traceReducer.ts` 把流量归约成 `RunTrace`，UI 按 step / tool / final / usage 分卡片渲染。

---

---

## 目录速查

```
src/
├─ agentengine/                     ← 框架代码，不依赖任何业务
│  ├─ spec.py      AgentSpec(frozen dataclass)
│  ├─ base/        AgentRun · AgentContext · AgentState
│  ├─ runtime/     turn.py(run_turn 唯一循环) · turn_runner · run_state · events
│  ├─ llm/         OpenAICompatibleClient · 环境变量入口 · 协议
│  ├─ memory/      Message · Memory(自动裁剪)
│  ├─ tools/       Tool · Collection · Registry · Executor + builtin/
│  ├─ stream/      EventStream · Printer · EventType
│  ├─ prompts/     YAML 提示词加载器(带缓存)
│  ├─ skills/      Claude Code 风格 SKILL.md 扫描
│  ├─ observability/ JSONL 运行日志
│  ├─ enterprise/  审批门 · 配额 · 中间件 · 租户 · 链路追踪
│  ├─ hooks/       生命周期钩子管理器
│  ├─ persistence/ 抽象持久化接口
│  ├─ concurrency/ 会话级并发锁
│  └─ errors.py    AgentEngineError 家族
│
├─ agents/                         ← Agent 规格声明
│  ├─ __init__.py  REGISTRY: dict[str, AgentSpec]
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
```

---

## 典型使用场景

### 场景 1：快速问答
```python
service = AgentOrchestrationService()
result = await service.run(agent_name="general_chat", query="你好")
```

### 场景 2：深度研究（多步工具调用）
```python
result = await service.run(
    agent_name="deep_research",
    query="分析 src/agentengine/ 的架构设计",
)
# LLM 自动拆解任务 → read_file → Skill → 综合报告
```

### 场景 3：接入 Web SSE
```python
context, event_stream = service.create_streaming_context(
    request_id="web-123",
    query="...",
    conversation_id="conv-456",
)
# event_stream 是异步可迭代对象，直接喂给 HTTP SSE 响应
```

### 场景 4：企业级管控
```python
from agentengine.enterprise.approval import ApprovalGate
from agentengine.enterprise.quota import QuotaStore, quota_middleware

gate = ApprovalGate(timeout_seconds=300)
store = QuotaStore()
store.set_limits("acme", QuotaLimits(max_runs=5, max_tool_calls=20))

# 注入中间件链
mw = MiddlewareChain([approval_middleware(gate), quota_middleware(store)])
service = AgentOrchestrationService(middleware=mw)
```

---

## 下一步

- 想理解架构？→ [架构详解](architecture.md)
- 想跑起来？→ [快速开始](quick-start.md)
- 想看某个模块？→ [文档首页](README.md) 选择对应模块文档
