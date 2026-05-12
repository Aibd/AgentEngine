# Reference App

> **这不是核心引擎的一部分。** 这是一个参考实现，用于本地测试、演示和集成参考。`agentengine` 发布包不包含此目录。

## 用途

- 演示如何把 `agentengine` 嵌入一个真实应用（FastAPI + SSE）。
- 给本仓库自带的 React 前端（`web/`）和 CLI 脚本（`scripts/`）提供后端入口。
- 作为业务方集成时的「抄一份改一改」起点。

## 不要这样用

- ❌ 不要 `from examples.reference_app import ...`：模块路径随时可能变。
- ❌ 不要把 `agent_orchestration_service.py` 当成 SDK 的一部分依赖：它只是 `AgentEngine` 的一层旧式包装，留给老脚本兼容用。
- ❌ 不要把这里的认证/路由/会话处理方式当成最佳实践：reference app 没有认证、没有租户隔离、`conversation_id` 处理也很简化。

## 正确的集成方式

复制需要的代码片段到你自己的服务里，按业务系统的约定改造（DI 容器、配置中心、认证中间件、租户路由等）。引擎本身的契约见 [INTEGRATION.md](../../INTEGRATION.md) 和 [docs/PUBLIC_API.md](../../docs/PUBLIC_API.md)。

## 目录

| 路径 | 说明 |
|------|------|
| `agents/` | 示例 `AgentPreset` 注册表（`general_chat`、`deep_research`） |
| `services/agent_orchestration_service.py` | 给老脚本用的 `AgentEngine` 包装 |
| `services/web_api.py` | FastAPI + SSE 端点示例 |

## 启动

```bash
uv run --env-file .env uvicorn examples.reference_app.services.web_api:app --host 127.0.0.1 --port 8000
```
