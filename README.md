# AgentEngine

AgentEngine 是一个可嵌入业务系统的 Agent 运行引擎。核心库只负责 Agent 运行时：LLM 调用、ReAct 循环、工具调度、事件流、持久化端口和会话锁。认证、鉴权、租户路由、HTTP/SSE/WebSocket 端点由宿主业务系统负责。

## 安装

```bash
uv sync --extra dev
```

核心发布包只包含 `agentengine`。Web/FastAPI 参考实现依赖放在 optional extra / dev 依赖中。

## 最小 SDK 用法

```python
from agentengine import AgentContext, AgentEngine, AgentPreset

engine = AgentEngine(
    presets={"chat": AgentPreset(name="chat", instructions="Answer briefly.")}
)

context = AgentContext(
    request_id="req-1",
    query="hello",
    llm=your_llm_client,
    conversation_id="conv-1",
)

answer = await engine.run(agent_name="chat", query=context.query, context=context)
```

更多集成方式见 [INTEGRATION.md](INTEGRATION.md)。

## Reference App

仓库内保留一个参考实现，用于本地测试和演示，不是核心引擎边界：

```text
examples/reference_app/
  agents/       示例 AgentPreset 注册表
  services/     兼容旧脚本的应用层 service + FastAPI/SSE 示例
web/            React/Vite 测试前端
scripts/        CLI 示例
```

启动参考后端：

```bash
uv run --env-file .env uvicorn examples.reference_app.services.web_api:app --host 127.0.0.1 --port 8000
```

启动前端：

```bash
cd web && npm install && npm run dev
```

## 示例

```bash
uv run python examples/minimal_chat.py
uv run python examples/custom_tool_and_persistence.py
```

使用真实 OpenAI-compatible LLM：

```bash
LLM_API_KEY=sk-your-key
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com

uv run --env-file .env python scripts/chat.py general_chat "你好"
```

## 目录

```text
src/agentengine/        核心 SDK，唯一发布包
examples/reference_app/ 参考应用，不随核心包发布
docs/                   架构、API、生产化说明
tests/                  pytest 测试
web/                    本地测试前端
```

## 文档

- [INTEGRATION.md](INTEGRATION.md): 业务系统集成指南
- [docs/PUBLIC_API.md](docs/PUBLIC_API.md): 公共 API 和兼容边界
- [docs/API.md](docs/API.md): 核心契约速览
- [docs/STREAMING_PROTOCOL.md](docs/STREAMING_PROTOCOL.md): SSE 参考协议
- [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md): 生产化注意事项

## 测试

```bash
uv run --env-file .env pytest
```

真实 LLM 集成测试需要 API Key：

```bash
uv run --env-file .env pytest -m integration
```
