# AgentKit Refactor

Agent 模块重构的独立脚手架。位于现有的 `gjsk_wiseagent_ai` 项目之外，以便新的核心可以在不破坏 FileClerk 或当前深度研究行为的情况下独立演进。

## 目录结构

```
src/
  agentkit/    框架代码，不依赖业务服务导入
    base/        AgentRun + AgentContext + AgentState
    runtime/     run_turn() + TurnRunner
    llm/         OpenAI 兼容客户端、Mock 客户端契约、环境变量工厂
    memory/      Message + Memory（支持裁剪 + 多模态）
    prompts/     PromptLoader（缓存 YAML）
    stream/      Printer + SSE v2 桥接
    tools/       Tool / ToolCollection / Registry / ReadFileTool / SkillTool
  agents/        业务 AgentSpec 声明 + 显式 REGISTRY
  services/      面向应用的入口点
tests/         覆盖每个框架模块的 pytest 测试套件
```

## 架构决策

### 循环逻辑放在 Handler 中，而不是 Agent 中

`BaseAgent` 是一个纯上下文容器，持有 `memory`、`state`、`current_step`，以及子类可以覆盖的三个钩子：

- `setup()`：注册工具、初始化记忆、注入额外数据
- `system_prompt()`：返回系统提示词字符串
- `next_step_prompt()`：可选的每轮指导

真正的 think→act 循环运行在 `runtime/turn.py::run_turn()` 中。Agent 保持声明式：`src/agents/*/spec.py` 定义 `AgentSpec`，`src/agents/__init__.py` 的 `REGISTRY` 负责显式注册。

### 工具调用以原始 OpenAI 字典形式端到端流动

`LLMResponse.tool_calls` 和 `Message.tool_calls` 都携带 OpenAI 原始字典格式（`{id, type, function: {name, arguments}}`）。中间没有 `ToolCall` 数据类。这意味着写入记忆的助手消息可以在下一轮原样发回给 LLM。

### 流式 tool_calls 按索引累积

`OpenAICompatibleClient._collect_stream` 在返回前从 `delta.tool_calls[*].function.arguments` 片段重建完整的 `tool_calls`。Handler 永远不会看到不完整的工具调用。

### Printer 信封与现有 SSE 契约匹配

`Printer` 发出包含 `responseType / response / responseAll / useTimes / reqId / errorMsg / resultMap / conversation_id / finished` 的字典，因此前端无需更改。

升级后的 trace 协议专为 Claude Code / Codex 风格的静态卡片设计：`start -> step -> thinking/text/tool_call_start/tool_result -> step_end -> usage -> result`。Web 渲染器应按步骤分组事件，在工具运行时将其显示为卡片，并在完成后将卡片冻结在原位，而不是重写历史。

### 运行时事件是内部诊断信息

`TurnRunner` 将语义运行时事件与公共 SSE 流分开记录。`RuntimeEvent` 对象位于 `agentkit.runtime.events` 下；SSE 协议名称位于 `agentkit.stream.events.EventType` 下。保持这些层分离，以便内部生命周期模型可以在不改变前端契约的情况下演进。

在推出或故障恢复期间，设置 `USE_LEGACY_RUNNER=true` 以绕过 `TurnRunner` 并使用原始的服务到 Handler 路径：

```powershell
$env:USE_LEGACY_RUNNER='true'
uv run --extra dev pytest -q
Remove-Item Env:\USE_LEGACY_RUNNER
```

### 运行事件日志

运行时事件追加到以下路径的 jsonl 文件中：

```text
${AGENTKIT_LOG_DIR:-logs}/runs/<YYYY-MM-DD>/<run_id>.jsonl
```

每行是一个序列化的运行时事件，包含 `event_type`、`run_id`、`turn_id` 和时间戳。对于本地调试，检查 `context.extras["run_event_log_path"]` 引用的文件。如果提供商报告上下文窗口失败，应将其表示为 `LLMContextWindowError`；`TurnRunner` 将其记录为 `terminal_reason="context_exceeded"`，以便后续规划可以在添加任何 TokenBudget 或压缩层之前使用真实数据。

## 迁移规则

不要先移动或删除旧代码。添加适配器和兼容性 Handler，然后在契约测试通过后再切换调用方。

推荐的首个集成路径：

1. 通过 `LegacyHandler` 保持现有的 `PlanSolveHandlerImpl` 可访问。
2. 在其旁边注册新 Agent（`general_chat`、`deep_research`）。
3. 通过 `FileClerkAdapter` 包装 FileClerk；在此阶段不要重写 FileClerk 内部。
4. SSE 字段稳定性由 `Printer` 本身强制执行。

## 运行测试

```bash
uv sync --extra dev
uv run --env-file .env pytest
```

`pyproject.toml` 设置了 `pythonpath = ["src"]` 和 `asyncio_mode = "auto"`，因此不需要额外配置。

真实提供商的冒烟测试是可选的：

```bash
# 首先在 .env 中设置 RUN_INTEGRATION=1。
uv run --env-file .env pytest -m integration
```

## API 文档

有关 Agent 生命周期、LLM 客户端、结构化错误、流式事件、注册表、记忆和编排的公共契约，请参阅 `docs/API.md`。
