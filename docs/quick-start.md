# 快速开始

> 5 分钟让 AgentEngine 跑起来：环境准备、CLI 聊天、Web 界面、写第一个 Agent。

---

## 1. 环境准备

### 1.1 安装依赖

```bash
# 使用 uv（推荐）
uv sync --extra dev

# 或使用 pip
pip install -e ".[dev]"
```

### 1.2 配置环境变量

创建 `.env` 文件：

```bash
LLM_API_KEY=sk-your-api-key-here
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1   # 可选，默认就是这个
```

支持的模型提供商：任何 OpenAI 兼容端点（DeepSeek、OpenAI、Azure、本地 vLLM 等）。

### 1.3 验证安装

```bash
uv run --env-file .env pytest -q
```

全部通过即可开始。

---

## 2. CLI 聊天

### 3.1 简单模式（原始事件）

```bash
PYTHONIOENCODING=utf-8 uv run python scripts/chat.py general_chat "你好"
```

### 3.2 漂亮模式（Rich 卡片）

```bash
# 普通对话
PYTHONIOENCODING=utf-8 uv run python scripts/chat_pretty.py general_chat "你好"

# 深度研究（显示推理过程）
PYTHONIOENCODING=utf-8 uv run python scripts/chat_pretty.py deep_research "分析 src/agentengine/ 的架构设计" --show-reasoning expanded
```

终端输出示例：

```
─── 🤖 deep_research  分析 src/agentengine/ 的架构设计 ───

  ▸ Turn 1
    💭 Thinking (推理内容已折叠)
┌─ 🔧 read_file ─────────────────────┐
│ { "path": "src/agentengine/runtime/turn.py" } │
└────────────────────────────────────┘
┌─ ✓ read_file · Result  (0.04s) ───┐
│ # Single-loop turn execution...    │
└────────────────────────────────────┘

  ▸ Turn 2
┌─ 📝 Answer ────────────────────────┐
│ AgentEngine 采用声明式架构...        │
└────────────────────────────────────┘
┌──────── ✓ Done ────────────────────┐
│         Total tokens  1,247        │
│             Duration  3.20s        │
└────────────────────────────────────┘
```

---

## 4. Web 界面

### 4.1 启动后端

```bash
uv run --env-file .env uvicorn examples.reference_app.services.web_api:app --host 127.0.0.1 --port 8000
```

### 4.2 启动前端

```bash
cd web
npm install   # 首次
npm run dev   # http://localhost:5173
```

### 4.3 访问

打开浏览器访问 `http://localhost:5173`，输入问题即可看到流式响应。

前端特性：
- 折叠思考链（Thinking）
- 工具调用卡片（Tool Call / Tool Result）
- 用量统计（Usage）
- 错误友好提示（Friendly Errors）

---

## 5. 写你的第一个 Agent

### 5.1 创建 spec 文件

```bash
mkdir -p src/agents/my_agent
```

`src/agents/my_agent/spec.py`：

```python
from agentengine.base.context import AgentContext
from agentengine.spec import AgentSpec

_SYSTEM_PROMPT = "你是一个专业的 Python 代码审查助手。检查代码中的潜在问题并给出建议。"

async def _setup(context: AgentContext) -> None:
    # 这里可以注册工具、初始化记忆等
    pass

SPEC = AgentSpec(
    name="my_agent",
    description="Python 代码审查 Agent",
    system_prompt=_SYSTEM_PROMPT,
    auto_compact_tokens=120_000,
    setup=_setup,
)
```

### 5.2 注册到 REGISTRY

编辑 `src/agents/__init__.py`：

```python
from agents.deep_research.spec import SPEC as DEEP_RESEARCH_SPEC
from agents.general_chat.spec import SPEC as GENERAL_CHAT_SPEC
from agents.my_agent.spec import SPEC as MY_AGENT_SPEC  # 新增

REGISTRY: dict[str, AgentSpec] = {
    "general_chat": GENERAL_CHAT_SPEC,
    "deep_research": DEEP_RESEARCH_SPEC,
    "my_agent": MY_AGENT_SPEC,  # 新增
}
```

### 5.3 运行

```bash
PYTHONIOENCODING=utf-8 uv run python scripts/chat_pretty.py my_agent "审查这段代码..."
```

---

## 6. 写你的第一个 Tool

### 6.1 实现 Tool

`src/agentengine/tools/builtin/my_tool.py`：

```python
from agentengine.tools.base import Tool

class MyTool(Tool):
    name = "my_tool"
    description = "A demo tool that greets the user."
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name to greet"}
        },
        "required": ["name"]
    }
    timeout_seconds = 5.0

    async def run(self, **kwargs) -> str:
        name = kwargs.get("name", "world")
        return f"Hello, {name}!"
```

### 6.2 注册

在 `src/agentengine/tools/builtin/__init__.py` 或其他合适位置：

```python
from agentengine.tools.registry import register_tool
from agentengine.tools.builtin.my_tool import MyTool

register_tool("my_tool")(MyTool)
```

### 6.3 接入 Agent

在 Agent 的 setup 函数中：

```python
from agentengine.tools.builtin.my_tool import MyTool

async def _setup(context: AgentContext) -> None:
    context.tool_collection.add(MyTool())
```

---

## 7. 以库形式使用

```python
import asyncio
from agentengine.llm.factory import create_llm_from_env
from agentengine import AgentContext, AgentEngine, AgentPreset

async def main():
    # 1. 创建 LLM
    llm = create_llm_from_env(required=True)

    # 2. 创建服务
    service = AgentEngine(presets={"general_chat": AgentPreset(name="general_chat")})

    # 3. 创建流式上下文
    context, event_stream = service.create_streaming_context(
        request_id="quick-test-001",
        query="请用中文简要介绍一下这个项目",
        conversation_id="test-conv-001",
    )
    context.llm = llm

    # 4. 在后台运行 Agent
    task = asyncio.create_task(
        service.run(agent_name="general_chat", query=context.query, context=context)
    )

    # 5. 消费 SSE 事件
    async for event in event_stream:
        print(f"[{event['event']}] {event.get('data', {})}")

    # 6. 获取最终结果
    result = await task
    print(f"Final: {result}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 8. 常见问题

### Q: 没有 API Key 能运行吗？
可以运行测试（使用 MockLLM），但不能调用真实模型。设置 `LLM_API_KEY` 即可。

### Q: 支持哪些模型？
任何 OpenAI 兼容的 API 端点：DeepSeek、OpenAI、Azure OpenAI、智谱、本地 vLLM / Ollama 等。

### Q: 日志在哪里？
运行时事件日志：`logs/runs/<YYYY-MM-DD>/<run_id>.jsonl`
应用日志：`logs/web-api.log`

---

## 下一步

- 深入了解架构 → [架构详解](architecture.md)
- 查看模块文档 → [文档首页](README.md)
- 了解 SSE 协议 → [STREAMING_PROTOCOL.md](STREAMING_PROTOCOL.md)
