# Base

`src/agentengine/base/` contains the mutable per-run objects:

- `AgentRun`: runtime state, memory, current turn counter, lifecycle state
- `AgentContext`: request dependencies such as LLM, tools, persistence, user,
  printer, and `extras`
- `AgentState`: `IDLE`, `RUNNING`, `FINISHED`, `ERROR`, `CANCELLED`

Reusable execution settings live in `RunConfig`, not on `AgentRun`.

---

## AgentContext

**文件：** `src/agentengine/base/context.py`

`AgentContext` 是每次 agent 运行的请求上下文，承载所有外部依赖。创建方式有两种：

```python
# 方式一：手动创建
from agentengine import AgentContext

context = AgentContext(
    request_id="req-123",        # 必填，请求唯一标识
    query="帮我查一下订单状态",     # 必填，用户输入
    llm=llm_client,              # LLM 客户端实例
    conversation_id="conv-456",  # 会话 ID，用于多轮对话和历史加载
)

# 方式二：通过引擎创建（自动配置流式通道）
context, event_stream = engine.create_streaming_context(
    request_id="req-123",
    query="帮我查一下订单状态",
    conversation_id="conv-456",
)
```

### 字段详解

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `request_id` | `str` | 是 | 请求唯一标识，用于日志追踪、取消操作、SSE 元数据。实际项目中通常用 `uuid.uuid4()` 生成，Web 项目建议从中间件透传 `X-Request-ID` 头。 |
| `query` | `str` | 是 | 用户输入的问题文本。 |
| `llm` | `LLMClient \| None` | 否 | LLM 客户端实例。可直接传入，或通过 `AgentEngine(llm_factory=...)` 注入回调提供。详见 [llm.md](llm.md)。 |
| `printer` | `Printer \| None` | 否 | SSE 流式输出接收器。通过 `create_streaming_context()` 自动创建，手动创建时一般不设置。 |
| `tool_collection` | `ToolCollection` | 否 | 工具注册表，Agent 可用的工具集合。通常在 Agent 的 `setup` 函数中添加。 |
| `session_id` | `str` | 否 | 会话标识，用于日志和锁管理。未设置时回退到 `conversation_id` 或 `request_id`。 |
| `conversation_id` | `str` | 否 | 会话 ID，用于多轮对话历史加载和会话级并发锁。 |
| `user` | `Any` | 否 | 当前用户对象，业务系统自行解析和使用，引擎不干预。 |
| `db` | `Any` | 否 | 数据库句柄，业务系统自行注入，供工具或 Agent 读写数据。 |
| `persistence` | `PersistencePort \| None` | 否 | 可插拔的持久化后端，用于记忆存储。 |
| `extras` | `dict[str, Any]` | 否 | 运行时扩展字段，引擎在执行过程中自动填充（`agent`、`run_config`、`cancellation_token`、`emit` 等）。 |

### request_id 生成示例

```python
import uuid

# UUID4（最常用）
request_id = str(uuid.uuid4())

# 带前缀的短 ID（便于日志搜索）
request_id = f"req-{uuid.uuid4().hex[:12]}"

# 基于时间的有序 ID（适合高并发场景）
import time
request_id = f"req-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
```

### llm_client 注入方式

```python
from agentengine.llm.env import create_llm_from_env
from agentengine.llm.openai_compat import OpenAICompatibleClient

# 方式一：从环境变量创建（推荐）
context.llm = create_llm_from_env(required=True)

# 方式二：手动创建
context.llm = OpenAICompatibleClient(
    api_key="sk-xxx",
    base_url="https://api.deepseek.com/v1",
    model="deepseek-chat",
)

# 方式三：通过引擎注入回调提供
engine = AgentEngine(llm_factory=lambda: create_llm_from_env(required=True))
# run() 时自动注入到 context.llm
```

---

## RunConfig

`RunConfig` is immutable and contains:

- `name`
- `initial_messages`
- `max_messages`
- `auto_compact_tokens`
- `compaction_keep_recent`
- optional `compactor`
- `setup` / `teardown`
- `extras`

`max_steps` / `max_turns` have been removed. Stop behavior belongs to runtime
signals: natural model completion, hooks, compaction errors, cancellation, and
provider/runtime errors.
