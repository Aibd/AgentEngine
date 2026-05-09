# AgentKit 真实LLM运行指南

## 🚀 快速开始

### 1. 确保环境变量已设置

在项目根目录创建或编辑 `.env` 文件：

```bash
# 必需的环境变量
LLM_API_KEY=your_api_key_here
LLM_MODEL=deepseek-chat  # 或 gpt-4、qwen-turbo 等

# 可选的环境变量
LLM_BASE_URL=https://api.deepseek.com  # 默认为DeepSeek
LLM_TIMEOUT=120
LLM_MAX_RETRIES=2
AGENTKIT_LOG_DIR=logs
USE_LEGACY_RUNNER=false
```

### 2. 安装依赖

```bash
# 使用uv安装（推荐）
uv sync --extra dev

# 或者使用pip
pip install -e ".[dev]"
```

### 3. 运行方式

#### 方式一：使用CLI脚本（推荐）

```bash
# 基础用法
python scripts/chat.py general_chat "你的问题"

# 指定最大步骤数
python scripts/chat.py general_chat "你的问题" --max-steps 5

# 启用跟踪模式（查看流式事件和内存）
python scripts/chat.py general_chat "你的问题" --trace

# 使用深度研究代理
python scripts/chat.py deep_research "详细研究主题"
```

#### 方式二：使用快速测试脚本

```bash
python run_agent.py
```

#### 方式三：在代码中调用

```python
import asyncio
from agentkit.base.context import AgentContext
from agentkit.llm.factory import create_llm_from_env
from services.agent_orchestration_service import AgentOrchestrationService

async def main():
    # 1. 创建LLM客户端
    llm = create_llm_from_env(required=True)
    
    # 2. 创建服务
    service = AgentOrchestrationService()
    
    # 3. 创建上下文
    context, event_stream = service.create_streaming_context(
        request_id="my-request",
        query="你的问题",
        conversation_id="my-conversation"
    )
    context.llm = llm
    
    # 4. 运行代理
    result = await service.run(
        agent_name="general_chat",
        query="你的问题",
        context=context
    )
    
    print(f"结果: {result}")
    
    # 5. 清理资源
    await llm.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## 📋 可用代理

可用代理在 `src/agents/__init__.py` 的 `REGISTRY` 中显式列出；每个代理的默认参数在对应的 `src/agents/*/spec.py` 中声明。

## 🔧 配置选项

### 环境变量

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `LLM_API_KEY` | ✅ | - | LLM API密钥 |
| `LLM_MODEL` | ✅ | - | 模型名称 |
| `LLM_BASE_URL` | ❌ | `https://api.deepseek.com` | API端点 |
| `LLM_TIMEOUT` | ❌ | `120` | 超时时间(秒) |
| `LLM_MAX_RETRIES` | ❌ | `2` | 最大重试次数 |
| `AGENTKIT_LOG_DIR` | ❌ | `logs` | 日志目录 |
| `USE_LEGACY_RUNNER` | ❌ | `false` | 使用传统运行器 |

### 代理配置

新增代理时，创建 `src/agents/my_agent/spec.py`，导出 `SPEC = AgentSpec(...)`，再把它加入 `src/agents/__init__.py` 的 `REGISTRY`。

## 🎯 常见使用场景

### 1. 简单问答

```bash
python scripts/chat.py general_chat "什么是机器学习？"
```

### 2. 深度研究

```bash
python scripts/chat.py deep_research "分析人工智能的发展趋势"
```

### 3. 流式输出（实时查看响应）

```bash
python scripts/chat.py general_chat "写一首关于AI的诗" --trace
```

### 4. 自定义步骤数

```bash
python scripts/chat.py general_chat "复杂问题" --max-steps 10
```

## 🐛 故障排除

### 1. 错误：Missing LLM environment variables

```bash
❌ 缺少必要的环境变量: LLM_API_KEY, LLM_MODEL
```

**解决方案：** 确保在 `.env` 文件中设置了这些变量。

### 2. 错误：连接超时

```bash
❌ LLMTimeoutError: Request timed out
```

**解决方案：**
- 增加 `LLM_TIMEOUT` 值
- 检查网络连接
- 确认API端点可访问

### 3. 错误：认证失败

```bash
❌ LLMHTTPError: 401 Unauthorized
```

**解决方案：** 检查 `LLM_API_KEY` 是否正确。

### 4. 错误：模型未找到

```bash
❌ LLMHTTPError: 404 Model not found
```

**解决方案：** 检查 `LLM_MODEL` 是否正确。

## 📊 高级功能

### 1. 查看运行日志

```bash
# 日志文件位置
ls logs/runs/

# 查看特定运行日志
cat logs/runs/2024-01-01/run_xxx.jsonl
```

### 2. 使用传统模式

```bash
# 使用传统运行器（兼容旧版本）
USE_LEGACY_RUNNER=true python scripts/chat.py general_chat "问题"
```

### 3. 自定义工具

查看 `src/agentkit/tools/builtin/` 了解内置工具，或创建自定义工具。

## 🔌 集成到应用

### FastAPI 集成示例

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from services.agent_orchestration_service import AgentOrchestrationService
from agentkit.base.context import AgentContext

app = FastAPI()
service = AgentOrchestrationService()

class ChatRequest(BaseModel):
    query: str
    agent_name: str = "general_chat"
    conversation_id: str = ""

@app.post("/chat")
async def chat(request: ChatRequest):
    try:
        context, _ = service.create_streaming_context(
            request_id="api-request",
            query=request.query,
            conversation_id=request.conversation_id
        )
        
        result = await service.run(
            agent_name=request.agent_name,
            query=request.query,
            context=context
        )
        
        return {
            "response": result,
            "conversation_id": context.conversation_id,
            "metadata": {
                "steps": context.extras.get("agent_current_step", 0),
                "state": context.extras.get("agent_state", "unknown")
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### WebSocket 流式示例

```python
from fastapi import WebSocket, WebSocketDisconnect
import asyncio

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    
    try:
        data = await websocket.receive_json()
        query = data.get("query", "")
        
        # 创建流式上下文
        context, event_stream = service.create_streaming_context(
            request_id="ws-request",
            query=query,
            conversation_id=data.get("conversation_id", "")
        )
        
        # 启动代理运行（异步）
        async def run_agent():
            await service.run(
                agent_name="general_chat",
                query=query,
                context=context
            )
        
        # 并发运行代理和流式输出
        agent_task = asyncio.create_task(run_agent())
        
        # 流式输出事件
        async for event in event_stream:
            if event is None:
                break
            await websocket.send_json(event)
        
        await agent_task
        
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_json({"error": str(e)})
```

## 📚 更多资源

1. **API文档**: `docs/API.md`
2. **架构设计**: 查看项目README
3. **测试示例**: `tests/` 目录
4. **代理注册**: `src/agents/__init__.py`

## 🎉 开始使用

现在你已经了解了如何运行AgentKit，尝试以下命令开始体验：

```bash
# 1. 设置环境变量
# 编辑 .env 文件

# 2. 运行简单测试
python scripts/chat.py general_chat "你好，请介绍一下自己"

# 3. 启用跟踪模式查看详细输出
python scripts/chat.py general_chat "你好" --trace

# 4. 尝试深度研究
python scripts/chat.py deep_research "机器学习基础"
```

祝你使用愉快！🚀
