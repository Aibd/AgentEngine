# AgentEngine 文档中心

> 本项目文档的完整导航页。从这里出发，你可以了解 AgentEngine 的每个细节。

---

## 📚 文档地图

### 入门与概览

| 文档 | 内容 | 适合谁 |
|------|------|--------|
| [总体介绍](overview.md) | 项目定位、核心能力、产品形态 | 所有人 |
| [架构详解](architecture.md) | 分层架构、数据流、时序图、设计取舍 | 开发者、架构师 |
| [快速开始](quick-start.md) | 5 分钟跑起来、CLI、Web、环境配置 | 新手 |

### AgentEngine 核心模块文档

> `src/agentengine/` 下每一个包的独立详解，包含设计意图、核心类、使用示例。

| 文档 | 模块 | 一句话概括 |
|------|------|-----------|
| [base.md](agentengine/base.md) | `base/` | AgentSpec + AgentRun + AgentContext + AgentState —— Agent 的「声明」与「运行时」 |
| [runtime.md](agentengine/runtime.md) | `runtime/` | TurnRunner + run_turn() —— 唯一的 think→act 循环 |
| [llm.md](agentengine/llm.md) | `llm/` | LLMClient 协议 + OpenAI 兼容客户端 + 环境变量工厂 |
| [memory.md](agentengine/memory.md) | `memory/` | Message + Memory —— 带自动裁剪的对话记忆 |
| [tools.md](agentengine/tools.md) | `tools/` | Tool / Collection / Registry / Executor + 所有内置工具 |
| [stream.md](agentengine/stream.md) | `stream/` | EventStream + Printer + SSE v2 协议桥接 |
| [prompts.md](agentengine/prompts.md) | `prompts/` | PromptLoader —— 带缓存的 YAML 提示词加载器 |
| [skills.md](agentengine/skills.md) | `skills/` | SkillLoader —— 扫描 `.agent/skills/SKILL.md` |
| [observability.md](agentengine/observability.md) | `observability/` | RunEventLog —— JSONL 运行事件日志 |
| [errors.md](agentengine/errors.md) | `errors.py` | AgentEngineError 家族 —— 可序列化、可重试标记 |
| [enterprise.md](agentengine/enterprise.md) | `enterprise/` | 审批门、配额限制、中间件链、租户隔离、链路追踪 |
| [hooks.md](agentengine/hooks.md) | `hooks/` | HookManager —— 可插拔的生命周期钩子 |
| [persistence.md](agentengine/persistence.md) | `persistence/` | PersistencePort —— 抽象的持久化接口 |
| [concurrency.md](agentengine/concurrency.md) | `concurrency/` | ConversationLockManager —— 会话级并发锁 |

### 使用指南

| 文档 | 内容 |
|------|------|
| [创建新 Agent](guides/create-agent.md) | 从复制 general_chat 到注册上线 |
| [创建新 Tool](guides/create-tool.md) | 继承 Tool、注册、接入 Agent |
| [接入自定义前端](guides/streaming-integration.md) | 消费 SSE v2 流、按 event 名称分发 |

### 协议与规范

| 文档 | 说明 |
|------|------|
| [API.md](API.md) | 公共契约：AgentSpec、LLMClient、工具、运行时事件、SSE 信封 |
| [STREAMING_PROTOCOL.md](STREAMING_PROTOCOL.md) | SSE v2 流式协议完整规范 |
| [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) | 生产环境检查清单 |
| [REFACTOR_PLAN.md](archive/REFACTOR_PLAN.md) | 重构路线图（Phase 1-3）（归档） |
| [REFACTOR_INVENTORY.md](archive/REFACTOR_INVENTORY.md) | 重构清单与进度（归档） |
| [TRACE_UI_STRATEGY.md](TRACE_UI_STRATEGY.md) | Trace UI 渲染策略 |

---

## 🗂️ 记忆口诀

一条请求的旅行路线：

```
services → runtime → (base · llm · memory · tools) → stream
   ↑___________________________________________________↓
                    （SSE 事件流返回前端）
```

---

## 🔄 文档维护

本文档跟随代码演进。如果代码有改动，对应模块文档也应同步更新。

> 核心源码路径：`src/agentengine/`
> 业务 Agent 路径：`src/agents/`
> 服务入口路径：`src/services/`
