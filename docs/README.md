# Agent 框架

> ⚠️ **此文档正在重构中。** Phase 2（Agent 数据化）+ Phase 3（单循环函数）已完成；Phase 4–5 正在进行。
> 当前架构：`AgentSpec + AgentRun` 取代 `BaseAgent + @register_agent`；`runtime/turn.py::run_turn()`
> 取代 `ReActHandler`，`handlers/` 目录已删除。
> 请参阅 [REFACTOR_PLAN.md](REFACTOR_PLAN.md) 了解完整路线图。

可复用的 Agent 基座框架，用于构建各类上层 AI 应用（深度研究、通用对话、文件处理等）。

---

## 目录结构

```
app/agent/
├── agentkit/                  # 核心框架（与业务无关）
│   ├── spec.py                  # AgentSpec —— 不可变 Agent 配置（frozen dataclass）
│   ├── base/                    # Agent 运行状态、上下文
│   │   ├── agent.py             # AgentRun —— 单次运行状态容器
│   │   ├── context.py           # AgentContext —— 单次运行环境
│   │   └── state.py             # AgentState —— 生命周期枚举
│   ├── llm/                     # LLM 客户端
│   │   ├── client.py            # LLMClient Protocol + LLMResponse/LLMChunk
│   │   ├── factory.py           # create_llm_from_env —— 环境变量创建客户端
│   │   └── openai_compat.py     # OpenAICompatibleClient —— 生产实现
│   ├── memory/                  # 对话记忆
│   │   ├── message.py           # Message —— 消息数据类
│   │   └── memory.py            # Memory —— 消息存储（支持 token 预算裁剪）
│   ├── tools/                   # 工具系统
│   │   ├── base.py              # Tool / StreamingTool / ToolStreamEvent
│   │   ├── executor.py          # ToolExecutor —— 工具执行器
│   │   ├── registry.py          # @register_tool 装饰器 + 全局注册表
│   │   ├── collection.py        # ToolCollection —— 每个 Agent 的工具集合
│   │   └── builtin/             # 内置工具
│   │       └── skill_tool.py    # SkillTool —— 技能调用
│   ├── runtime/                 # 运行时生命周期
│   │   ├── turn.py              # run_turn —— 唯一 think→act 循环函数
│   │   ├── turn_runner.py       # TurnRunner —— 单轮运行管理
│   │   ├── run_state.py         # RunState —— 运行状态跟踪
│   │   └── events.py            # RuntimeEvent —— 内部生命周期事件
│   ├── stream/                  # SSE 流式输出
│   │   ├── event_stream.py      # EventStream —— 异步队列
│   │   ├── events.py            # EventType —— SSE 协议事件名
│   │   └── printer.py           # Printer —— 事件格式化 + 发送
│   ├── prompts/                 # 提示词
│   │   └── loader.py            # PromptLoader —— YAML 提示词加载
│   ├── skills/                  # 技能系统
│   │   └── loader.py            # SkillLoader —— SKILL.md 扫描加载
│   ├── persistence/             # 持久化抽象
│   │   └── port.py              # PersistencePort —— 持久化接口定义
│   ├── observability/           # 可观测性
│   │   └── event_log.py         # RunEventLog —— JSONL 运行日志
│   └── errors.py                # 结构化错误层次
│
├── agents/                      # 具体 Agent 规格（业务相关）
│   ├── __init__.py              # REGISTRY: dict[str, AgentSpec] — 显式注册表
│   ├── general_chat/
│   │   └── spec.py              # SPEC = AgentSpec(name="general_chat", ...)
│   └── deep_research/
│       └── spec.py              # SPEC = AgentSpec(name="deep_research", ...)
│
└── services/                    # 应用层入口
    └── agent_orchestration_service.py  # AgentOrchestrationService
```

---

## 核心设计理念

### 1. AgentSpec（不可变配置）+ AgentRun（运行状态）

**AgentSpec** 是 `frozen=True` 的 dataclass——声明"这个 Agent 是什么"（名称、系统提示词、最大步数、setup/teardown 钩子）。它与运行无关，可以安全地跨请求共享。

**AgentRun** 是可变的 per-run 容器——持有 Memory、当前步数、AgentState。每次请求创建一个新实例。

```python
# agents/general_chat/spec.py
SPEC = AgentSpec(
    name="general_chat",
    system_prompt="You are a helpful assistant.",
    max_steps=3,
)

# 使用 dataclasses.replace() 从 YAML 覆盖字段（不修改冻结对象）
spec = replace(SPEC, max_steps=5)
agent = AgentRun(spec=spec, context=ctx)
```

### 2. 显式注册表

```python
# agents/__init__.py
from agents.general_chat.spec import SPEC as GENERAL_CHAT
from agents.deep_research.spec import SPEC as DEEP_RESEARCH

REGISTRY: dict[str, AgentSpec] = {
    GENERAL_CHAT.name: GENERAL_CHAT,
    DEEP_RESEARCH.name: DEEP_RESEARCH,
}
```

不再使用 `@register_agent` 装饰器——显式比隐式好，消除了导入顺序依赖。

### 3. 双层事件模型

| 层 | 事件类型 | 用途 |
|----|---------|------|
| 内部 | `RuntimeEvent`（RunStarted, TextDelta, ToolCallCompleted...） | 生命周期管理、可观测性 |
| 外部 | `EventType`（START, TEXT, TASK, TOOL_RESULT, RESULT...） | SSE 协议，前端消费 |

`Printer.from_runtime_event()` 负责将内部事件翻译为外部事件。

---

## 模块详解

### spec —— AgentSpec（不可变配置）

**AgentSpec** (`spec.py`)：冻结的 dataclass，声明 Agent 身份。

```python
@dataclass(frozen=True, slots=True)
class AgentSpec:
    name: str
    system_prompt: str = ""
    next_step_prompt: str = ""
    description: str = ""
    max_steps: int = 10          # >= 1
    max_messages: int = 0        # 0 = 不限
    setup: SetupHook | None = None    # async (ctx) -> None
    teardown: SetupHook | None = None
    extras: dict[str, object] = field(default_factory=dict)
```

### base —— AgentRun（运行状态）

**AgentRun** (`base/agent.py`)：可变的 per-run 容器，不实现循环逻辑。

```python
@dataclass(slots=True)
class AgentRun:
    spec: AgentSpec
    context: AgentContext      # 运行环境
    memory: Memory             # 对话记忆（由 spec.max_messages 初始化）
    current_step: int = 0      # 当前步数
    state: AgentState          # IDLE / RUNNING / FINISHED / ERROR / CANCELLED

    # 属性代理（从 spec 读取）
    @property
    def name(self) -> str: ...
    @property
    def max_steps(self) -> int: ...
    def system_prompt(self) -> str: ...
    def next_step_prompt(self) -> str: ...

    async def setup(self) -> None: ...    # 委托给 spec.setup(context)
    async def teardown(self) -> None: ... # 委托给 spec.teardown(context)
```

**AgentContext** (`base/context.py`)：单次运行的完整环境。

```python
@dataclass
class AgentContext:
    request_id: str
    query: str
    llm: LLMClient | None          # LLM 客户端
    printer: Printer | None         # SSE 事件发送器
    tool_collection: ToolCollection # 工具集合
    session_id: str
    conversation_id: str
    user: Any                       # 当前用户（DB 持久化用）
    db: Any                         # 数据库会话
    persistence: PersistencePort | None  # 持久化接口
    extras: dict[str, Any]          # 扩展字段
```

### runtime/turn —— 唯一的 think→act 循环

**run_turn()** (`runtime/turn.py`)：所有 Agent 共用的循环函数。

```
1. agent.setup()
2. 加载历史记忆（如果有 persistence）
3. 注入 system_prompt + 用户消息到 Memory
4. 循环（最多 spec.max_steps 次）：
   a. 构建消息列表（注入 next_step_prompt）
   b. 调用 LLM（流式）
   c. 如果 LLM 返回 tool_calls → 执行工具 → 结果写入 Memory → 继续循环
   d. 如果 LLM 返回纯文本 → 作为最终答案 → 退出循环
5. 保存记忆到 DB（如果有 persistence）
6. agent.teardown()
```

> Phase 1 已删除 `PipelineHandler` 和 `LegacyHandler`；Phase 3 把 `ReActHandler` 内联为 `run_turn()`，`handlers/` 目录不再存在。要扩展循环，直接修改函数或在 Service 中传入自定义 `turn_fn` 给 `TurnRunner.run()`。

### llm —— LLM 客户端

**LLMClient** (`llm/client.py`)：Protocol 接口。

```python
class LLMClient(Protocol):
    async def chat(self, messages, *, tools, stream, **kwargs) -> LLMResponse: ...
    async def chat_stream(self, messages, *, tools, **kwargs) -> AsyncIterator[LLMChunk]: ...
```

**OpenAICompatibleClient** (`llm/openai_compat.py`)：生产实现。
- 支持任意 OpenAI 兼容端点（DeepSeek、Qwen、vLLM 等）
- 自动重试（指数退避，可配置次数）
- 流式 SSE 解析 + tool_call delta 累积
- 错误分类：`LLMRateLimitError`、`LLMTimeoutError`、`LLMConnectionError` 等

**create_llm_from_env** (`llm/factory.py`)：从环境变量创建客户端。

```
LLM_API_KEY     # 必填
LLM_MODEL       # 必填
LLM_BASE_URL    # 可选，默认 https://api.deepseek.com
LLM_TIMEOUT     # 可选，默认 120s
LLM_MAX_RETRIES # 可选，默认 2
```

### memory —— 对话记忆

**Message** (`memory/message.py`)：单条消息。

```python
@dataclass
class Message:
    role: Role                     # SYSTEM / USER / ASSISTANT / TOOL
    content: str
    reasoning_content: str         # DeepSeek 思考内容
    tool_call_id: str | None
    tool_calls: list[dict] | None  # OpenAI 格式
    base64_image: str | None       # 多模态图片

    def to_openai(self) -> dict: ...       # 序列化为 OpenAI 格式
    @classmethod
    def from_openai(cls, data) -> Message: ...  # 从 OpenAI 格式反序列化
```

**Memory** (`memory/memory.py`)：消息存储。

```python
@dataclass
class Memory:
    messages: list[Message]
    max_messages: int = 0    # 消息条数上限（0=不限）
    max_tokens: int = 0      # Token 预算上限（0=不限）

    def append(self, message): ...     # 添加并自动裁剪
    def snapshot(self) -> list: ...    # 获取快照（线程安全）
    def to_openai(self) -> list: ...   # 全量序列化

    async def load_from_db(self, persistence, conversation_id): ...
    async def save_to_db(self, persistence, conversation_id): ...
```

裁剪策略：
- 系统消息（`role=SYSTEM`）始终保留
- 当 `max_messages > 0` 时，超出时删除最旧的非系统消息
- 当 `max_tokens > 0` 时，从最新消息向前保留，直到 token 超限
- 两个限制同时生效时，取更严格的那个

---

### tools —— 工具系统

#### Tool 基类 (`tools/base.py`)

```python
class Tool(ABC):
    name: str
    description: str
    schema: dict                       # JSON Schema（参数定义）
    timeout_seconds: float = 30.0      # 执行超时
    is_destructive: bool = False       # 是否有副作用
    max_result_chars: int = 8000       # 结果最大字符数
    result_summary_strategy: str = "truncate"  # 截断策略

    def to_openai_tool(self) -> dict: ...  # 转为 OpenAI function calling 格式

    @abstractmethod
    async def run(self, **kwargs) -> Any: ...  # 子类实现
```

#### StreamingTool 流式工具 (`tools/base.py`)

用于需要中间事件推送的工具（如深度搜索）。

```python
class StreamingTool(Tool):
    @abstractmethod
    async def run_stream(self, **kwargs) -> AsyncGenerator[ToolStreamEvent, None]:
        """yield 中间事件，最后一个事件 is_final=True"""
        yield ToolStreamEvent(event_type="search_result", data={...})
        yield ToolStreamEvent(event_type="final_result", data={...}, is_final=True)
```

**ToolStreamEvent**：
- `event_type: str` — 对应 Printer 事件名（`"tool_thought"`, `"search_result"`, `"final_result"` 等）
- `data: Any` — 事件载荷
- `is_final: bool` — 是否最终结果

#### ToolExecutor 工具执行器 (`tools/executor.py`)

统一封装工具执行的关注点：超时、事件发射、结果截断。

```python
executor = ToolExecutor(
    run_id="...",
    turn_id="...",
    on_event=runtime_event_callback,       # RuntimeEvent 回调
    on_stream_event=stream_event_callback, # 流式中间事件回调（转发到 Printer）
    timeout_seconds=30,
)

# 自动检测工具类型：
# - 普通 Tool → 调用 run()
# - StreamingTool → 调用 run_stream()，中间事件通过 on_stream_event 推送
result = await executor.execute(tool, arguments)
```

#### ToolRegistry 工具注册表 (`tools/registry.py`)

```python
@register_tool("my_tool")
class MyTool(Tool): ...

# 创建实例
tool = create_tool("my_tool")

# 列出所有已注册工具
tools = registered_tools()
```

#### ToolCollection 工具集合 (`tools/collection.py`)

每个 Agent 实例持有一个 ToolCollection，管理该 Agent 可用的工具。

```python
collection = ToolCollection()
collection.add(MyTool())
collection.get("my_tool")        # 按名称获取
collection.require("my_tool")    # 获取或抛异常
collection.to_openai_tools()     # 转为 OpenAI 格式列表
```

---

### 内置工具详解

#### 设计原则：模型原生规划

本框架**不使用显式的规划工具**（如旧系统的 `PlanningTool`）。规划是模型的内生能力：

- 模型通过 system prompt 理解任务，自行拆解为子步骤
- ReAct 循环天然支持"想一步做一步"的逐步执行
- 规划状态隐含在对话历史和模型推理中，无需外部状态管理
- 工具只负责**执行动作**（搜索、读文件、调 API），不负责元认知

这与 Claude Code、Codex 等现代 Agent 的设计理念一致。

#### SkillTool —— 技能调用工具

让 LLM 调用预定义的技能（SKILL.md 文件）。

```python
# LLM 调用:
{"skill": "code_review", "args": "review this PR"}

# SkillTool 返回该技能的指令文本，注入到对话中
```

技能文件位于 `.agent/skills/*/SKILL.md`，YAML frontmatter + Markdown body。

---

### registry —— 注册系统

**Agent Registry**：已替换为 `agents/__init__.py` 中的显式 `REGISTRY` dict（见"如何新增一个 Agent"）。`@register_agent` 装饰器已删除。

**Handler Registry**：Phase 3 已删除——循环唯一，无需注册策略。`@register_tool` 仍然在 `tools/registry.py` 提供工具注册。

### runtime —— 运行时

**TurnRunner** (`runtime/turn_runner.py`)：管理单次 Agent 运行的完整生命周期。

```
1. 生成 run_id / turn_id
2. 创建 RunState（PENDING）
3. 创建 RunEventLog（JSONL 日志）
4. 发射 RunStarted
5. 调用 handler.handle()
6. 成功 → RunCompleted / 取消 → RunCancelled / 异常 → RunFailed（带错误分类）
```

错误分类：
- `LLMContextWindowError` → CONTEXT_EXCEEDED
- `ToolExecutionError` → TOOL_FAILED
- `LLMError` → MODEL_FAILED
- 其他 → RUNTIME_FAILED

**RuntimeEvent** (`runtime/events.py`)：内部生命周期事件。

```
RunStarted     — 运行开始
TextDelta      — 流式文本片段
ToolCallStarted   — 工具调用开始
ToolCallCompleted — 工具调用完成
ToolCallFailed    — 工具调用失败
RunCompleted   — 运行完成
RunFailed      — 运行失败
RunCancelled   — 运行取消
```

### stream —— SSE 流式输出

**EventStream** (`stream/event_stream.py`)：基于 `asyncio.Queue` 的异步迭代器。

```python
stream = EventStream()
await stream.put({"key": "value"})  # 生产者
async for event in stream:          # 消费者
    print(event)
await stream.close()                # 关闭
```

**EventType** (`stream/events.py`)：SSE 协议事件名。

| EventType | 说明 | finished |
|-----------|------|----------|
| `START` | 开始处理 | false |
| `TEXT` | 文本片段 | false |
| `TASK` | 子任务描述 | false |
| `TOOL_THOUGHT` | 工具思考过程 | false |
| `TOOL_RESULT` | 工具执行结果 | false |
| `SEARCH_RESULT` | 搜索结果 | false |
| `FINAL_RESULT` | 最终结果流 | false |
| `RESULT` | 最终结果 | **true** |
| `ERROR` | 错误 | **true** |
| `DONE` | 完成 | **true** |

**Printer** (`stream/printer.py`)：事件格式化器。

```python
printer = Printer(request_id="xxx", event_stream=stream, conversation_id="yyy")

await printer.start(query)           # → eventType=START, response="开始处理: ..."
await printer.text(content)          # → eventType=TEXT
await printer.task(description)      # → eventType=TASK
await printer.tool_result(tool, res) # → eventType=TOOL_RESULT
await printer.result(data)           # → eventType=RESULT, finished=True
await printer.error(exc)             # → eventType=ERROR, finished=True
await printer.done()                 # → eventType=DONE, finished=True
```

输出 dict 结构（与前端 SSE 契约一致）：
```text
event: text
data: {"delta":"开始处理: 用户的问题","request_id":"xxx","conversation_id":"yyy"}
```

### persistence —— 持久化抽象

**PersistencePort** (`persistence/port.py`)：Protocol 接口。

```python
class PersistencePort(Protocol):
    async def save_run(self, *, run_id, conversation_id, agent_name, input_msg, reply_msg, metadata): ...
    async def save_messages(self, conversation_id, messages: list[dict]): ...
    async def load_messages(self, conversation_id) -> list[dict]: ...
    async def save_artifact(self, run_id, artifact_type, data): ...
```

实现类在 `app/services/agent_persistence_impl.py`（基于 SQLAlchemy），通过 `AgentContext.persistence` 注入。

### errors —— 错误层次

```
AgentKitError
├── LLMError
│   ├── LLMHTTPError
│   │   └── LLMRateLimitError    (retryable, 429)
│   ├── LLMTimeoutError          (retryable)
│   ├── LLMConnectionError       (retryable)
│   ├── LLMStreamError           (retryable)
│   └── LLMContextWindowError    (不可重试)
├── ToolExecutionError
└── RuntimeExecutionError
    └── AgentCancelledError
```

每个错误携带：`code`, `category`, `retryable`, `status_code`, `details`。

---

## 如何新增一个 Agent

### 第一步：创建 spec.py

```python
# agents/my_agent/spec.py
from agentkit.spec import AgentSpec
from agentkit.base.context import AgentContext

async def _setup(context: AgentContext) -> None:
    """运行前初始化：注册工具等"""
    # context.tool_collection.add(MyTool())
    pass

SPEC = AgentSpec(
    name="my_agent",
    description="我的自定义 Agent",
    system_prompt=(
        "你是一个专业的助手。"
        "面对复杂任务，先拆解为子任务，再逐步执行。"
    ),
    next_step_prompt="继续执行下一个子任务。如果所有子任务已完成，输出最终结果。",
    max_steps=10,
    setup=_setup,
)
```

### 第二步：加入 REGISTRY

```python
# agents/__init__.py
from agents.my_agent.spec import SPEC as MY_AGENT

REGISTRY: dict[str, AgentSpec] = {
    ...,
    MY_AGENT.name: MY_AGENT,
}
```

### 第三步：调用

```python
service = AgentOrchestrationService()
context, event_stream = service.create_streaming_context(
    request_id="xxx",
    query="用户的请求",
    conversation_id="yyy",
)
result = await service.run(agent_name="my_agent", query="用户的请求", context=context)
```

---

## 如何新增一个工具

### 普通工具

```python
from agentkit.tools.base import Tool
from agentkit.tools.registry import register_tool

@register_tool("my_tool")
class MyTool(Tool):
    name = "my_tool"
    description = "做某件事"
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "查询内容"}
        },
        "required": ["query"]
    }

    async def run(self, **kwargs) -> str:
        query = kwargs["query"]
        # ... 执行逻辑 ...
        return "结果"
```

### 流式工具

```python
from agentkit.tools.base import StreamingTool, ToolStreamEvent
from agentkit.tools.registry import register_tool

@register_tool("my_streaming_tool")
class MyStreamingTool(StreamingTool):
    name = "my_streaming_tool"
    description = "流式执行某件事"
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"}
        },
        "required": ["query"]
    }

    async def run_stream(self, **kwargs):
        query = kwargs["query"]

        # 中间事件 → 通过 Printer 推送到前端
        yield ToolStreamEvent(
            event_type="tool_thought",
            data="正在搜索..."
        )

        # 搜索结果事件
        yield ToolStreamEvent(
            event_type="search_result",
            data={"results": [...]}
        )

        # 最终结果（is_final=True）
        yield ToolStreamEvent(
            event_type="final_result",
            data="完整的分析报告...",
            is_final=True
        )
```

---

## 与旧系统对比

| 维度 | 旧系统 (`services/multi_agent/`) | 新框架 (`agent/`) |
|------|------|------|
| Agent 模型 | 自实现 think/act 循环 | 纯数据容器，循环在 Handler |
| Handler | `PlanSolveHandlerImpl` 硬编码 | 可插拔 react/pipeline/legacy |
| LLM | httpx 手写 SSE | OpenAICompatibleClient（重试/错误分类） |
| 工具 | plan_tool + deep_search | skill_tool + StreamingTool 基类（模型原生规划） |
| 注册 | handler_map 硬编码 | 装饰器自动注册 |
| 持久化 | handler 内直接写 DB | PersistencePort 抽象接口 |
| 可观测性 | 无 | RunEventLog JSONL + RuntimeEvent |
| 错误处理 | 简单 try/except | 结构化错误层次 + retryable 标记 |

---

## 待完成项

- [ ] DeepSearchTool 具体实现（基于 StreamingTool）
- [ ] PersistenceImpl 完善（save_messages/load_messages 实际读写 DB）
- [ ] 路由层对接（routers/agents.py 切换到新框架）
- [ ] 并发隔离（同一 conversation 的请求排队）
- [ ] 取消 API（通过 conversation_id 取消运行中的 Agent）
- [ ] 测试套件
