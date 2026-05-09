# llm — LLM 客户端与工厂

> `src/agentengine/llm/` 提供与大型语言模型交互的抽象层：一个极简的 `LLMClient` 协议、一个 OpenAI 兼容的实现、以及从环境变量创建客户端的工厂。

---

## 模块组成

```
llm/
├── client.py        # LLMClient Protocol + LLMResponse + LLMChunk
├── openai_compat.py # OpenAICompatibleClient（httpx 实现）
├── factory.py       # create_llm_from_env() 环境变量工厂
└── __init__.py
```

---

## LLMClient — 抽象协议

**文件：** `src/agentengine/llm/client.py`

### 设计意图

AgentEngine 不绑定任何特定 LLM 提供商。只要实现 `LLMClient` 协议，就可以接入框架。

```python
class LLMClient(Protocol):
    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMResponse: ...

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]: ...
```

### 数据类

#### LLMResponse（非流式响应）

```python
@dataclass(slots=True)
class LLMResponse:
    content: str = ""                        # 助手回复正文
    reasoning_content: str = ""              # DeepSeek 等模型的推理链
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # OpenAI 原始格式
    finish_reason: str | None = None         # "stop" / "tool_calls" / ...
    usage: dict[str, int] = field(default_factory=dict)  # token 用量
    raw: dict[str, Any] | None = None        # 原始响应备份
```

#### LLMChunk（流式增量）

```python
@dataclass(slots=True)
class LLMChunk:
    content: str = ""                        # 文本增量
    reasoning_content: str = ""              # 推理增量
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] | None = None        # 原始 chunk 备份
```

**关键设计：** `tool_calls` 在 `LLMResponse` 中保持 OpenAI 原始字典格式 `{id, type, function: {name, arguments}}`。这样可以直接写入 `Message`，并在下一轮原样发回给 LLM，**无需任何转换层**。

---

## OpenAICompatibleClient

**文件：** `src/agentengine/llm/openai_compat.py`

### 功能

基于 `httpx.AsyncClient` 的 OpenAI 兼容客户端，支持：
- 标准 `chat.completions` API
- 流式输出（`stream=True`）
- 工具调用（`tools` / `tool_choice`）
- 自动重试（`max_retries`）
- 超时控制（`timeout`）

### 构造参数

```python
client = OpenAICompatibleClient(
    base_url="https://api.deepseek.com/v1",
    api_key="sk-...",
    model="deepseek-chat",
    timeout=120.0,
    max_retries=2,
)
```

### 流式 tool_calls 的累积

OpenAI 的流式响应中，tool_calls 是按 `index` 分片到达的：

```json
{"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"p"}}]}}]}
{"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "ath\""}}]}}]}
```

`OpenAICompatibleClient._collect_stream` 内部维护一个 `dict[int, dict]`，按 `index` 累积 `arguments` 片段，最终组装成完整的 `tool_calls` 列表。

**这意味着：框架上层（run_turn）永远不需要处理不完整的 tool_calls。**

---

## 工厂：create_llm_from_env

**文件：** `src/agentengine/llm/factory.py`

### 环境变量映射

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `LLM_API_KEY` | ✅ | — | API 密钥 |
| `LLM_MODEL` | ✅ | — | 模型名称 |
| `LLM_BASE_URL` | ❌ | `https://api.deepseek.com` | API 基础地址 |
| `LLM_TIMEOUT` | ❌ | `120.0` | 请求超时（秒） |
| `LLM_MAX_RETRIES` | ❌ | `2` | 失败重试次数 |

### 用法

```python
from agentengine.llm.factory import create_llm_from_env

# 严格模式：缺少变量直接抛 LLMConfigError
llm = create_llm_from_env(required=True)

# 宽松模式：缺少变量返回 None
llm = create_llm_from_env(required=False)
if llm is None:
    print("LLM not configured, running in dry mode")
```

### 自定义前缀

```python
# 读取 DEEPSEEK_API_KEY / DEEPSEEK_MODEL 等
llm = create_llm_from_env(prefix="DEEPSEEK_")
```

---

## 接入新的 LLM 提供商

如果你想接入非 OpenAI 格式的模型（如 Claude Native、Gemini、本地模型），有两种方式：

### 方式一：适配到 OpenAI 格式（推荐）

大多数提供商都提供了 OpenAI 兼容层。只需改 `LLM_BASE_URL` 和 `LLM_MODEL`：

```bash
# Azure OpenAI
LLM_BASE_URL=https://your-resource.openai.azure.com/openai/deployments/your-deployment
LLM_API_KEY=your-azure-key
LLM_MODEL=gpt-4

# 本地 Ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3
```

### 方式二：实现 LLMClient 协议

```python
from agentengine.llm.client import LLMClient, LLMResponse, LLMChunk
from agentengine.memory.message import Message

class ClaudeNativeClient(LLMClient):
    async def chat(self, messages, *, tools=None, stream=False, **kwargs):
        # 调用 Claude API
        return LLMResponse(content="...")

    async def chat_stream(self, messages, *, tools=None, **kwargs):
        # 调用 Claude Streaming API
        yield LLMChunk(content="Hello")
        yield LLMChunk(content=" world")
```

然后注入到 `AgentContext`：

```python
context.llm = ClaudeNativeClient()
```

---

## 错误处理

LLM 层可能抛出以下异常（均继承 `LLMError`）：

| 异常 | 触发条件 | retryable |
|------|---------|-----------|
| `LLMHTTPError` | HTTP 非 2xx | 视状态码 |
| `LLMRateLimitError` | 429 Too Many Requests | ✅ |
| `LLMTimeoutError` | 请求超时 | ✅ |
| `LLMConnectionError` | 网络断开 | ✅ |
| `LLMStreamError` | 流解析失败 | ✅ |
| `LLMContextWindowError` | 上下文超长 | ❌ |

详见 [errors.md](errors.md)。

---

## 测试辅助：MockLLMClient

项目测试中使用 `tests/mock_llm.py` 提供的 Mock 客户端，可以：
- 预定义响应序列
- 验证传入的 messages / tools
- 模拟流式输出

```python
from tests.mock_llm import MockLLMClient

mock = MockLLMClient(responses=[
    LLMResponse(content="Hello!"),
])
context.llm = mock
```

---

## 关联文档

- [errors.md](errors.md) — LLMError 家族详情
- [memory.md](memory.md) — Message 如何转成 LLM 输入
- [runtime.md](runtime.md) — run_turn 如何调用 chat_stream
