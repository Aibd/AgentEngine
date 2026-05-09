# AgentEngine

声明式 Agent 执行框架。Agent 只需声明「用什么提示词、注册什么工具」，框架负责循环、LLM 调用、工具执行、记忆管理和事件流。

## 快速开始

### 1. 安装依赖

```bash
uv sync --extra dev
```

### 2. 配置环境变量

创建 `.env` 文件：

```bash
LLM_API_KEY=sk-your-api-key-here
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1   # 可选，默认 DeepSeek
```

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `LLM_API_KEY` | 是 | — | LLM API 密钥 |
| `LLM_MODEL` | 是 | — | 模型名称 |
| `LLM_BASE_URL` | 否 | `https://api.deepseek.com/v1` | API 端点 |
| `LLM_TIMEOUT` | 否 | `120` | 请求超时（秒） |
| `LLM_MAX_RETRIES` | 否 | `2` | 最大重试次数 |
| `AGENTENGINE_LOG_DIR` | 否 | `logs` | 日志目录 |

支持任何 OpenAI 兼容端点：DeepSeek、OpenAI、Azure、智谱、本地 vLLM / Ollama 等。

### 3. 运行

```bash
# 快速体验
uv run --env-file .env python run_agent.py

# CLI 聊天（原始事件）
PYTHONIOENCODING=utf-8 uv run python scripts/chat.py general_chat "你好"

# CLI 聊天（Rich 卡片，推荐）
PYTHONIOENCODING=utf-8 uv run python scripts/chat_pretty.py general_chat "你好"

# 深度研究
PYTHONIOENCODING=utf-8 uv run python scripts/chat_pretty.py deep_research "分析 src/agentengine/ 的架构"
```

### 4. Web 界面

```bash
# 后端
uv run --env-file .env uvicorn --app-dir src services.web_api:app --host 127.0.0.1 --port 8000

# 前端
cd web && npm install && npm run dev   # http://localhost:5173
```

## 目录结构

```
src/
  agentengine/                框架代码，不依赖业务
    base/        AgentRun + AgentContext + AgentState
    runtime/     run_turn() + TurnRunner + RuntimeEvent
    llm/         OpenAI 兼容客户端 + 环境变量工厂
    memory/      Message + Memory（裁剪 + 多模态）
    tools/       Tool / Collection / Registry + builtin/
    stream/      Printer + SSE v2 桥接
    enterprise/  审批门 · 配额 · 中间件 · 租户
    hooks/       生命周期钩子
  agents/        AgentSpec 声明 + 显式 REGISTRY
  services/      AgentOrchestrationService + Web API

scripts/         CLI 脚本（chat.py / chat_pretty.py）
web/             React + Vite 前端
tests/           pytest 测试套件
docs/            详细文档
```

## 核心设计

- **声明式 Agent** — `AgentSpec`（frozen dataclass）描述 Agent 身份，`REGISTRY` 显式注册
- **统一循环** — 唯一的 `run_turn()` 驱动所有 Agent，差异通过数据表达
- **OpenAI 原始 dict 端到端** — tool_calls 不做中间转换，直接塞回 LLM
- **双轨事件** — 内部 `RuntimeEvent` ↔ 公共 SSE 刻意分层，互不干扰
- **工具治理** — 统一超时、截断、审批、配额、破坏性检测

## 运行测试

```bash
uv sync --extra dev
uv run --env-file .env pytest
```

`pyproject.toml` 设置了 `pythonpath = ["src"]` 和 `asyncio_mode = "auto"`，无需额外配置。

集成测试（需真实 API Key）：

```bash
uv run --env-file .env pytest -m integration
```

## 故障排除

| 症状 | 原因 | 解决 |
|------|------|------|
| `Missing LLM environment variables` | `.env` 未配置 | 设置 `LLM_API_KEY` 和 `LLM_MODEL` |
| `LLMTimeoutError` | 请求超时 | 增大 `LLM_TIMEOUT`，检查网络 |
| `401 Unauthorized` | Key 错误 | 检查 `LLM_API_KEY` |
| `404 Model not found` | 模型名错误 | 检查 `LLM_MODEL` |

## 文档

- [快速开始（详细）](docs/quick-start.md) — 写第一个 Agent / Tool、以库形式使用
- [架构详解](docs/architecture.md) — 模块划分与数据流
- [模块文档](docs/README.md) — 按模块索引
- [SSE 协议](docs/STREAMING_PROTOCOL.md) — 流式事件规范
- [API 契约](docs/API.md) — 公共接口定义
