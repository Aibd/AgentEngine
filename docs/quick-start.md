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

### 7.1 核心概念：run() 的返回语义

`engine.run()` 的返回类型**始终是 `str`**——即完整的最终回答文本。它会等待整个 agent turn（可能包含多轮 LLM 调用 + 工具执行）结束后才返回。

```python
# 这行会阻塞直到 agent 完成所有工作
answer = await engine.run(agent_name="support", query="帮我查订单", context=context)
# answer 此时已经是完整回答，不是流式增量
```

**流式是旁路通道**，不影响 `run()` 的返回值。流式 token 通过 `SseEventQueue` 或 `on_event` 回调实时推送，与 `run()` 并行运行：

```python
# run() 在后台执行，同时 event_stream 实时推送 token
task = asyncio.create_task(engine.run(..., context=context))
async for frame in event_stream:
    print(frame)  # 实时收到 {"event": "text", "data": {"delta": "你"}}
answer = await task  # 最终完整文本
```

### 7.2 创建 LLM 客户端

`context.llm` 需要一个符合 `LLMClient` 协议的实例。框架自带 `OpenAICompatibleClient`，也支持自定义实现。

```python
from agentengine.llm.env import create_llm_from_env
from agentengine.llm.openai_compat import OpenAICompatibleClient

# 方式一：从环境变量创建（推荐）
llm = create_llm_from_env(required=True)  # 读取 LLM_API_KEY, LLM_MODEL, LLM_BASE_URL

# 方式二：手动创建
llm = OpenAICompatibleClient(
    api_key="sk-xxx",
    base_url="https://api.deepseek.com/v1",
    model="deepseek-chat",
    max_tokens=4096,
)

# 方式三：自定义实现（只需满足 Protocol）
class MyLLM:
    async def chat(self, messages, *, tools=None, stream=False, **kwargs):
        return LLMResponse(content="...")

    async def chat_stream(self, messages, *, tools=None, **kwargs):
        yield LLMChunk(content="Hello")
        yield LLMChunk(content=" world")

llm = MyLLM()
```

### 7.3 生成 request_id

`request_id` 是每次请求的唯一标识，用于日志追踪、取消操作和 SSE 元数据。实际项目中的常见做法：

```python
import uuid

# 方式一：UUID4（最常用，无冲突风险）
request_id = str(uuid.uuid4())
# "550e8400-e29b-41d4-a716-446655440000"

# 方式二：带前缀的短 ID（便于日志搜索）
request_id = f"req-{uuid.uuid4().hex[:12]}"
# "req-550e8400e29b"

# 方式三：基于时间的有序 ID（适合高并发场景按时间排序）
import time
request_id = f"req-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
# "req-1715500000000-a3f2c1"
```

在 Web 框架中，通常从中间件生成并透传：

```python
# FastAPI 中间件示例
from fastapi import Request
import uuid

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response
```

### 7.4 完整示例

```python
import asyncio
import uuid
from agentengine.llm.env import create_llm_from_env
from agentengine import AgentContext, AgentEngine, AgentPreset

async def main():
    # 1. 创建 LLM
    llm = create_llm_from_env(required=True)

    # 2. 创建服务
    service = AgentEngine(presets={"general_chat": AgentPreset(name="general_chat")})

    # 3. 创建流式上下文
    context, event_stream = service.create_streaming_context(
        request_id=str(uuid.uuid4()),
        query="请用中文简要介绍一下这个项目",
        conversation_id="test-conv-001",
    )
    context.llm = llm

    # 4. 在后台运行 Agent（run() 返回完整字符串）
    task = asyncio.create_task(
        service.run(agent_name="general_chat", query=context.query, context=context)
    )

    # 5. 消费 SSE 事件（实时流式）
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

### Q: engine.run() 是等全部回答完才返回吗？
是的。`run()` 返回类型始终是 `str`（完整最终回答），会等待整个 agent turn 结束后才返回。流式输出通过旁路通道（`SseEventQueue` 或 `on_event` 回调）实时推送，与 `run()` 并行运行。详见 [7.1 核心概念](#71-核心概念run-的返回语义)。

### Q: llm_client 是什么？必须自己实现吗？
不需要。`llm_client` 是符合 `LLMClient` Protocol 的实例，框架自带 `OpenAICompatibleClient` 实现，通过 `create_llm_from_env()` 即可从环境变量创建。如需接入非 OpenAI 格式的模型，只需实现 `chat()` 和 `chat_stream()` 两个异步方法。详见 [LLM 文档](agentengine/llm.md)。

### Q: request_id 怎么生成？
用 `uuid.uuid4()` 即可。Web 项目建议从中间件生成并透传 `X-Request-ID` 头。详见 [7.3 生成 request_id](#73-生成-request_id)。

---

## 下一步

- 深入了解架构 → [架构详解](architecture.md)
- 查看模块文档 → [文档首页](README.md)
- 了解 SSE 协议 → [STREAMING_PROTOCOL.md](STREAMING_PROTOCOL.md)
