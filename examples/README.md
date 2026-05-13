# 示例

本目录提供可运行的示例代码，用来演示如何在业务应用中接入 `agentengine`

## 目录结构

| 路径 | 用途 |
|------|------|
| `agents/` | 示例 Agent 预设，包含 `general_chat` 和 `deep_research` |
| `services/agent_orchestration_service.py` | 示例编排服务，将 Agent 预设接入 `AgentEngine` |
| `services/web_api.py` | FastAPI + SSE 示例接口，可用于本地 Web UI 或接口调试 |
| `full_sdk_example.py` | 端到端 SDK 示例，包含 LLM 客户端配置、Agent 预设、自定义工具、流式输出和 SQLite 持久化 |

## 运行完整 SDK 示例

推荐先在项目根目录配置 `.env`：

```bash
LLM_API_KEY=your_api_key_here
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1
```

然后运行：

```bash
uv run --env-file .env python examples/full_sdk_example.py
```

也可以不使用 `.env`，直接通过命令行参数传入配置：

```bash
uv run python examples/full_sdk_example.py \
  --base-url https://api.deepseek.com/v1 \
  --model deepseek-chat \
  --api-key your_api_key_here \
  --query "请先调用用户资料查询工具获取用户 U-100 的资料，然后总结查询结果。"
```

## 运行 Web API 示例

```bash
uv run --env-file .env uvicorn examples.services.web_api:app --host 127.0.0.1 --port 8000
```

启动后可以访问：

```text
http://127.0.0.1:8000
```

## 说明

这些示例用于帮助理解接入方式，不属于公开 SDK API 的稳定承诺范围。生产项目中建议从 `agentengine` 导入公开对象，按自己的业务创建 Agent 预设、工具、持久化实现和 Web 接口
