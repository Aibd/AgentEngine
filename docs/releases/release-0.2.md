# AgentEngine 0.2 版本说明

更新时间：2026-05-13  
版本：v0.2.0  
状态：可进入业务侧集成联调

AgentEngine 0.2 已经把 Agent 运行底座从示例/业务代码中抽离出来，形成可被业务系统直接接入的独立 SDK。业务侧可以基于公开 API 注册 Agent、注入 LLM、接入工具、消费流式事件，并按需启用持久化、并发控制和企业中间件能力。


## 本版本做了什么

### 1. SDK 化核心入口

- 提供 `agentengine.AgentEngine` 作为统一运行入口。
- 支持 `AgentDefinition` / `RunConfig` 声明 Agent 名称、指令、上下文压缩、初始化和清理逻辑。
- 示例层保留 `AgentOrchestrationService` 兼容包装，但新集成建议直接从 `agentengine` 包根导入公开对象。

### 2. Agent 运行闭环

- 完成 `AgentEngine -> TurnRunner -> run_turn -> LLMClient/ToolExecutor -> RuntimeEvent/SSE` 的运行链路。
- 支持 ReAct 风格的模型思考、工具调用、工具结果回写和继续推理。
- 支持运行取消：`AgentEngine.interrupt(request_id)`。
- 支持运行事件记录，便于排查、审计和前端展示。

### 3. LLM 接入

- 提供 OpenAI-compatible 客户端，可接入 DeepSeek、OpenAI、Qwen、vLLM、LiteLLM 代理等兼容 Chat Completions 的模型服务。
- 支持普通响应和流式响应。
- 支持 reasoning_content、tool_calls、usage 等模型返回字段。
- 对超时、限流、HTTP 错误、流式中断等异常做了结构化封装。

### 4. 工具体系

- 支持普通工具和流式工具。
- 支持工具超时、执行结果截断、错误事件、工具流式事件。
- 已内置常用工具能力，包括文件读取、文件写入、文件编辑、grep、glob、bash、TodoWrite、AskUserQuestion、Skill 等。
- 支持 `ExecPolicy` 和人工审批中间件，用于限制或确认高风险工具执行。

### 5. 流式协议和 Web 集成

- 提供 RuntimeEvent 事件模型。
- 提供 SSE v2 事件帧，适合直接接入 Web 前端或转成 WebSocket。
- 示例 Web API 已包含 FastAPI + SSE 接口。
- 示例前端可展示运行过程、文本流、工具调用、usage 和错误。

### 6. 持久化与会话

- 支持 SQLite 持久化。
- 支持按 `conversation_id` 保存和恢复会话消息。
- Web 示例支持按租户隔离 conversation。

### 7. 并发控制

- 提供内存会话锁。
- 提供 Redis 会话锁接口，适合多实例部署场景。
- 避免同一会话并发执行导致上下文互相覆盖。

### 8. 企业集成能力

- 支持租户上下文 `TenantContext`。
- 支持配额限制，包括运行次数、工具调用次数、token 输入输出等。
- 支持人工审批 `ApprovalGate`，适合高风险/破坏性工具。
- 支持重试中间件。
- 支持 OpenTelemetry 追踪。
- 支持 Secret 抽象，便于后续接入企业密钥管理。

## 当前验证结果

本地已完成以下检查：

- 后端测试：`368 passed, 6 skipped`
- 类型检查：`mypy` 通过，81 个源文件无问题
- Web 示例构建：`npm ci` 后 `npm run build` 通过
- SDK 导入烟测：`agentengine.AgentEngine` 可正常导入和初始化

跳过的 6 个用例为真实 LLM 服务商集成测试，需要配置真实 API Key 并设置 `RUN_INTEGRATION=1` 后执行。

## 接入建议

业务系统接入时建议只依赖公开 API：

```python
from agentengine import AgentContext, AgentEngine, AgentDefinition
```

不要直接依赖以下内部模块：

- `agentengine.runtime.*`
- `agentengine.base.*`
- `agentengine.stream.*`
- `examples.*`

推荐集成方式：

- 后端服务持有一个 `AgentEngine` 实例或按租户构造实例。
- 通过 `definitions` 注册业务 Agent。
- 通过 `llm_factory` 或 `AgentContext.llm` 注入模型客户端。
- 通过 `AgentContext.tool_collection` 或 `AgentDefinition.setup` 注册业务工具。
- 前端需要流式体验时，使用 `create_streaming_context()` 获取事件队列，再转 SSE 或 WebSocket。
- 生产多实例部署时，使用 Redis 会话锁替换内存锁。

## 已知注意事项

- 0.2 仍属于 0.x 阶段，公开 API 会尽量稳定，但 1.0 前 minor 版本仍可能存在必要的破坏性调整。
- 当前真实 LLM 集成测试默认跳过，发布到业务环境前需要补跑真实模型接口验证。
- `examples/` 仅作为接入参考，不承诺稳定；生产集成应使用 `agentengine` 包根公开 API。
- 高风险工具建议必须接入审批或执行策略。
- 共享文档中收集的问题需要定期回流到 issue、任务或版本计划中，避免反馈只停留在表格里。

## 问题反馈

后续 Bug、需求、优化建议统一维护到共享文档：

[AgentEngine 反馈共享文档](https://docs.qq.com/sheet/DZUNpdHFMaHJMemNX?scene=56499fa331d9aef189ea8506xzcPw1&tab=BB08J2)

