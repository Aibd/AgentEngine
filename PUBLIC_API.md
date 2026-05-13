# Public API

**当前版本：v0.2.0**

本文档列出 `agentengine` 包的全部公开对象及其完整 API 说明，遵循 [SemVer](https://semver.org/lang/zh-CN/) 语义化版本：v0.x 阶段 minor 版本允许破坏性改动（CHANGELOG 会标注），v1.0 之后破坏性改动只在 major 版本发生。


## 目录

- [导入清单](#导入清单)
- [1. 引擎核心](#1-引擎核心)
- [2. 运行时上下文](#2-运行时上下文)
- [3. Agent 预设与运行配置](#3-agent-预设与运行配置)
- [4. 工具系统](#4-工具系统)
- [5. LLM 协议](#5-llm-协议)
- [6. 持久化](#6-持久化)
- [7. 会话锁](#7-会话锁)
- [8. 运行时事件](#8-运行时事件)
- [9. 错误系统](#9-错误系统)
- [10. Hook 系统](#10-hook-系统)
- [11. 企业中间件](#11-企业中间件)
- [12. 取消与压缩](#12-取消与压缩)
- [13. 流式输出](#13-流式输出)

---

## 导入清单

所有公开对象均可从 `agentengine` 包根直接导入：

```python
from agentengine import (
    # ── 核心引擎 ──────────────────────────────────────────
    AgentEngine,                         # 引擎门面：Agent 调度、ReAct 循环、事件分发
    AgentContext,                        # 请求上下文：承载 LLM、工具、用户等运行时依赖
    AgentPreset,                         # Agent 预设：声明名称、系统提示词和生命周期钩子
    RunConfig,                           # 运行配置：不可变的执行参数

    # ── 工具系统 ──────────────────────────────────────────
    Tool,                                # 工具基类：实现 schema + run() 即可注册
    StreamingTool,                        # 流式工具基类：支持逐步输出中间结果
    ToolStreamEvent,                      # 工具流式事件：StreamingTool 的中间产物
    ToolCollection,                       # 工具注册表：管理 Agent 可用的工具集合
    ExecPolicy,                           # 执行策略：白名单/黑名单规则引擎
    ExecPolicyAction,                     # 策略动作：ALLOW / DENY / ASK
    ExecPolicyRule,                       # 策略规则：前缀匹配 + 动作

    # ── LLM 协议 ──────────────────────────────────────────
    LLMClient,                            # LLM 客户端协议：实现 chat/chat_stream 即可接入
    LLMResponse,                          # 非流式响应：content + reasoning + tool_calls + usage
    LLMChunk,                             # 流式增量：单次 delta 文本/推理片段

    # ── 持久化 & 锁 ───────────────────────────────────────
    PersistencePort,                      # 持久化协议：消息/会话/制品的存储接口
    ConversationLockManager,              # 会话锁协议：防止同一会话并发执行
    InMemoryConversationLockManager,      # 内存锁实现（单进程）
    RedisConversationLockManager,         # Redis 分布式锁实现（多副本）

    # ── 运行时事件（全部 15 种）──────────────────────────
    RuntimeEvent,                         # 事件基类
    RunStarted, RunCompleted, RunFailed, RunCancelled,  # 运行生命周期
    TurnStarted, TurnEnded,               # 轮次边界
    TextDelta, ReasoningDelta,            # 文本/推理增量
    ToolCallStarted, ToolCallCompleted, ToolCallFailed,  # 工具调用生命周期
    ToolStreamEventEmitted,               # 工具流式中间事件
    UsageReport,                          # Token 用量报告
    ApprovalRequired,                     # 破坏性工具审批
    TodosUpdated,                         # 任务清单更新
    UserQuestionAsked,                    # Agent 向用户提问

    # ── 取消 & 压缩 ───────────────────────────────────────
    CancellationToken,                    # 取消令牌：协程协作式取消
    Compactor,                            # 压缩协议：可插拔的历史摘要器
    LLMSummaryCompactor,                  # LLM 压缩实现：用模型总结历史

    # ── 企业中间件 ────────────────────────────────────────
    MiddlewareChain,                      # 中间件链：洋葱模型组合多个中间件

    # ── 默认常量 ──────────────────────────────────────────
    DEFAULT_AGENT_SYSTEM_PROMPT,          # 默认安全指令（自动追加到系统提示词末尾）

    # ── 状态对象 ──────────────────────────────────────────
    AgentRun,                             # 运行记录：包含 run_id、状态、消息等
    AgentState,                           # 运行状态枚举
)
```

---

## 1. 引擎核心

### AgentEngine

引擎的唯一入口 负责 Agent 预设管理、LLM 注入、运行调度、流式事件分发和中断控制。

```python
class AgentEngine:
    def __init__(
        self,
        *,
        presets: dict[str, AgentPreset | SupportsRunConfig] | None = None,
        # Agent 预设注册表。key 为 agent_name，run() 时引用。
        # 与 config_resolver 二选一，都提供则 presets 优先。

        config_resolver: Callable[[str], RunConfig] | None = None,
        # 动态 RunConfig 解析器。每个 run() 调用时通过 agent_name 查询。
        # 适合从数据库/配置中心动态加载配置的场景。

        llm_factory: Callable[[], LLMClient] | None = None,
        # LLM 工厂回调。当 context.llm 为空时自动调用。
        # 适合 Web 服务中按租户/用户动态创建 LLM 客户端。

        require_llm: bool = True,
        # 是否要求 LLM 必须存在。False 时允许没有 LLM 也能运行（测试用）。

        max_query_chars: int = 20_000,
        # 用户输入最大字符数。超长输入将被截断。

        persistence: PersistencePort | None = None,
        # 持久化实现。注入后引擎会在 conversation_id 非空时自动加载/保存消息。

        lock_manager: ConversationLockManager | None = None,
        # 会话锁管理器。默认使用 InMemoryConversationLockManager（单进程）。
        # 多副本部署必须注入 RedisConversationLockManager。

        middleware: MiddlewareChain | None = None,
        # 企业中间件链。按需注入配额、审批、追踪、重试等横切关注点。
    ): ...

    # ── 核心方法 ──────────────────────────────────────────

    async def run(
        self,
        *,
        agent_name: str,
        # 要运行的 Agent 名称，对应 presets 中的 key。

        query: str,
        # 用户输入的问题/指令。

        context: AgentContext | None = None,
        # 运行时上下文。若提供，其中的 LLM、工具、持久化等会与引擎合并。
        # 若不提供，引擎根据 request_id 创建默认上下文。

        tool_timeout_seconds: float | None = 30.0,
        # 全局工具执行超时（秒）。会被单个 Tool 的 timeout_seconds 覆盖。

        agent_kwargs: dict[str, Any] | None = None,
        # 传递给 AgentPreset.setup 和 RunConfig 的额外参数。

        on_event: Callable[[RuntimeEvent], Awaitable[None]] | None = None,
        # 事件回调。每次引擎产生事件时调用，用于旁路推送到 SSE/WebSocket。
        # 返回的仍然是完整字符串结果。

    ) -> str:
        """
        执行一次 Agent 运行。返回完整的最终回答字符串。

        ReAct 循环没有内置步数上限，结束条件：
        1. 模型返回的响应不包含 tool_calls
        2. AfterTurn hook 返回 STOP 结果
        3. 自动压缩失败抛出 ContextWindowExceededError
        4. AgentEngine.interrupt() 取消运行
        5. LLM API 返回不可恢复的错误
        """

    def create_streaming_context(
        self,
        *,
        request_id: str,
        # 请求唯一标识。用于日志追踪和中断控制。

        query: str,
        # 用户输入的问题。

        conversation_id: str = "",
        # 会话 ID。非空时启用历史加载和持久化保存。

    ) -> tuple[AgentContext, SseEventQueue]:
        """
        创建流式上下文，返回 (context, event_stream)。

        event_stream 是异步队列，通过 SseEventQueue 实时推送事件帧。
        调用方用 async for frame in event_stream 消费，每个 frame 是
        {"event": str, "data": dict} 结构。

        engine.run() 应在 asyncio.create_task() 中后台运行，
        同时 event_stream 在前台被消费。
        """

    def interrupt(self, request_id: str, reason: str = "interrupted") -> bool:
        """
        取消正在运行的 Agent。

        request_id 与 AgentContext 中的 request_id 对应。
        返回 True 表示成功发送取消信号，False 表示未找到对应运行。
        """
```

---

## 2. 运行时上下文

### AgentContext

每次 `engine.run()` 的完整运行时参数容器。业务系统创建后注入引擎。

```python
@dataclass
class AgentContext:
    request_id: str
    # 请求唯一标识。用于日志追踪、中断控制和事件关联。

    query: str
    # 用户输入的原始问题。

    llm: LLMClient | None = None
    # LLM 客户端实例。若不提供，引擎会尝试使用 llm_factory 回调创建。
    # 必须实现 chat() 和 chat_stream() 两个异步方法。

    printer: Printer | None = None
    # 可选的渲染器。用于自定义文本/推理增量和系统消息的渲染方式。

    tool_collection: ToolCollection = field(default_factory=ToolCollection)
    # 工具注册表。Agent 可调用的工具集合。
    # 通过 context.tool_collection.add(my_tool) 注册自定义工具。

    session_id: str = ""
    # 可选的会话标识。与 conversation_id 的区别：session 可跨越多个 conversation。

    conversation_id: str = ""
    # 会话 ID。非空时引擎会在 run 前自动加载历史消息，run 后自动保存。
    # 同一 conversation_id 的并发调用会被会话锁串行化。

    user: Any = None
    # 当前用户对象。由业务系统解析后注入，用于审计和安全控制。
    # 类型不限，引擎不关心具体结构。

    db: Any = None
    # 可选的数据库连接。允许工具直接访问业务数据库。
    # 类型不限，业务系统自行管理。

    persistence: PersistencePort | None = None
    # 持久化实现。优先级高于引擎级别的 persistence。
    # 适合每个请求使用不同持久化后端的场景。

    extras: dict[str, Any] = field(default_factory=dict)
    # 扩展字段。业务系统可注入任意额外数据（如 tenant 信息、trace context 等）。
    # 常用 key：
    #   "hooks"  → HookManager 实例
    #   "tenant" → TenantContext 实例
    #   "exec_policy" → ExecPolicy 实例
```

---

## 3. Agent 预设与运行配置

### AgentPreset

业务系统声明 Agent 的入口。是一个不可变 dataclass，通过 `to_run_config()` 编译为 `RunConfig`。

```python
@dataclass(frozen=True, slots=True)
class AgentPreset:
    name: str
    # Agent 名称。run() 时通过 agent_name 引用。
    # 全局唯一，建议使用小写下划线命名（如 "customer_support"）。

    description: str = ""
    # Agent 的可读描述。用于 UI 展示和日志。

    instructions: str = ""
    # 系统提示词。引擎会自动追加 DEFAULT_AGENT_SYSTEM_PROMPT
    # （"持续工作直到完成"的安全指令）。

    max_messages: int = 0
    # 消息历史上限。0 表示不限制。
    # 超过后最早的非系统消息会被移除。

    auto_compact_tokens: int = 0
    # 自动压缩阈值。消息历史 token 数超过此值时触发自动压缩。
    # 0 表示禁用压缩。建议值：120_000（为 API 上下文窗口留余量）。

    compaction_keep_recent: int = 8
    # 压缩时保留最近多少条消息不被摘要。
    # 值越大保留的上下文越完整，但压缩效果越差。

    compactor: Compactor | None = None
    # 自定义压缩器。不提供则使用 LLMSummaryCompactor。

    setup: Callable[[AgentContext], Awaitable[None]] | None = None
    # 启动钩子。在 run() 开始时调用，用于注册工具、初始化记忆等。
    # 签名：async def my_setup(context: AgentContext) -> None

    teardown: Callable[[AgentContext], Awaitable[None]] | None = None
    # 清理钩子。在 run() 结束时调用，用于释放资源、记录日志等。
    # 签名：async def my_teardown(context: AgentContext) -> None

    extras: dict[str, object] = field(default_factory=dict)
    # Agent 级别的扩展配置。传递到 RunConfig.extras。
```

### RunConfig

编译后的不可变运行配置。通常不直接创建，由 `AgentPreset.to_run_config()` 生成。

```python
@dataclass(frozen=True, slots=True)
class RunConfig:
    name: str                              # Agent 名称（同 AgentPreset.name）
    initial_messages: tuple[Message, ...]  # 初始消息（至少包含 system 消息）
    max_messages: int = 0                  # 消息上限
    auto_compact_tokens: int = 0           # 压缩阈值
    compaction_keep_recent: int = 8        # 压缩保留最近条数
    compactor: Compactor | None = None     # 压缩器
    setup: SetupHook | None = None         # 启动钩子
    teardown: SetupHook | None = None      # 清理钩子
    extras: dict[str, object]              # 扩展配置
```

---

## 4. 工具系统

### Tool

所有工具的抽象基类。业务系统继承此类实现自定义工具。

```python
class Tool(ABC):
    name: str
    # 工具名称。LLM 调用时通过此名称引用。
    # 全局唯一（在同一 ToolCollection 内），建议使用小写下划线。

    description: str
    # 工具描述。告诉 LLM 这个工具什么时候应该被调用、能做什么。
    # 影响 LLM 的工具选择质量，建议写清楚触发条件。

    schema: dict[str, Any]
    # JSON Schema 格式的参数定义。
    # 示例：
    # {
    #     "type": "object",
    #     "properties": {
    #         "order_id": {"type": "string", "description": "订单 ID"}
    #     },
    #     "required": ["order_id"],
    # }

    timeout_seconds: float = 30.0
    # 单次执行超时（秒）。超时后工具调用被标记为失败。

    is_destructive: bool = False
    # 是否破坏性工具。设为 True 后：
    # 1. 引擎会在执行前发送 ApprovalRequired 事件
    # 2. ExecPolicy 可单独控制破坏性工具的审批流程

    max_result_chars: int = 50_000
    # 结果最大字符数。超过后按 result_summary_strategy 截断。

    result_summary_strategy: str = "truncate"
    # 结果截断策略。可选 "truncate"（截断）、"summarize"（LLM 摘要）。

    # ── 方法 ──────────────────────────────────────────────

    def to_openai_tool(self) -> dict[str, Any]:
        """转为 OpenAI 兼容的工具定义。包含 type: function + name + description + parameters。"""

    @abstractmethod
    async def run(self, **kwargs) -> str:
        """执行工具逻辑。参数由 LLM 根据 schema 传入。返回结果字符串。"""
```

### StreamingTool

支持逐步输出中间结果的工具。适用于长时间运行的工具（如搜索、代码生成等）。

```python
class StreamingTool(Tool):
    async def run_stream(self, **kwargs) -> AsyncIterator[ToolStreamEvent]:
        """
        逐步执行工具逻辑。子类必须实现此方法。

        必须 yield 至少一个 is_final=True 的 ToolStreamEvent。
        每个事件作为 ToolStreamEventEmitted 推送到客户端。

        示例：
            yield ToolStreamEvent(event_type="searching", data="搜索中...")
            yield ToolStreamEvent(event_type="found", data="找到 3 条结果")
            yield ToolStreamEvent(
                event_type="result", data=final_result, is_final=True
            )
        """

    async def run(self, **kwargs) -> str:
        """默认实现：消费 run_stream() 并拼接所有事件数据。"""
```

### ToolStreamEvent

流式工具的中间事件。

```python
@dataclass
class ToolStreamEvent:
    event_type: str = "tool_thought"
    # 事件类型。映射到下游 ToolStreamEventEmitted 的 event_type 字段。

    data: Any
    # 事件内容。可以是字符串、字典等任意类型。

    is_final: bool = False
    # 是否为最终事件。True 表示工具执行完成，后续不再有新事件。
```

### ToolCollection

工具注册表。线程安全。

```python
class ToolCollection:
    def __init__(self, tools: list[Tool] | None = None): ...
    # 创建集合，可选地预填充工具列表。

    def add(self, tool: Tool) -> None:
        """注册工具。按 tool.name 去重（后注册的覆盖先注册的）。"""

    def get(self, name: str) -> Tool | None:
        """按名称获取工具。不存在返回 None。"""

    def require(self, name: str) -> Tool:
        """按名称获取工具。不存在抛出 KeyError。"""

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """将所有工具转为 OpenAI 兼容列表。"""
```

### ExecPolicy

工具执行前的安全策略引擎。通过前缀规则实现白名单/黑名单。

```python
class ExecPolicy:
    def __init__(
        self,
        rules: Iterable[ExecPolicyRule] | None = None,
        default: ExecPolicyAction = ExecPolicyAction.ALLOW,
    ): ...

    def decide(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> ExecPolicyDecision:
        """
        评估工具是否允许执行。
        按 rules 顺序匹配，命中第一个规则后返回其决策。
        未命中任何规则时返回 default 动作。
        shell 类工具优先用 arguments["command"] 匹配，其余用 tool_name。
        """

class ExecPolicyAction(enum.Enum):
    ALLOW = "allow"  # 允许执行
    DENY = "deny"    # 拒绝执行
    ASK = "ask"      # 需要用户确认（触发 ApprovalRequired 事件）

@dataclass
class ExecPolicyRule:
    prefix: str | tuple[str, ...]   # 匹配前缀
    action: ExecPolicyAction        # 匹配后的动作
    reason: str = ""                # 策略说明（出现在拒绝消息中）

    def matches(self, subject: str) -> bool:
        """subject 是否以此前缀开头。"""
```

---

## 5. LLM 协议

### LLMClient (Protocol)

LLM 客户端协议。任何实现 `chat()` 和 `chat_stream()` 的对象均可注入。

```python
class LLMClient(Protocol):
    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        **kwargs,
    ) -> LLMResponse:
        """非流式对话。返回完整响应（包含 content、tool_calls、usage 等）。"""

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs,
    ) -> AsyncIterator[LLMChunk]:
        """流式对话。yield 增量 Token 片段。"""
```

框架自带 `OpenAICompatibleClient` 实现，通过 `create_llm_from_env()` 从环境变量 `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` 创建。

### LLMResponse

非流式对话的完整响应。

```python
@dataclass
class LLMResponse:
    content: str                      # 主文本响应
    reasoning_content: str            # 推理/思维链文本
    tool_calls: list[dict[str, Any]]  # 工具调用列表（OpenAI 格式）
    finish_reason: str | None         # 停止原因（stop / length / tool_calls 等）
    usage: dict[str, int]             # Token 用量（prompt_tokens / completion_tokens / total_tokens）
    raw: dict[str, Any] | None        # 原始 API 响应（调试用）
```

### LLMChunk

流式对话的单个 Token 增量。

```python
@dataclass
class LLMChunk:
    content: str                      # 文本增量片段
    reasoning_content: str            # 推理增量片段
    finish_reason: str | None         # 停止原因（仅在最后一个 chunk 中有值）
    usage: dict[str, int]             # Token 用量（仅在最后一个 chunk 中有值）
    raw: dict[str, Any] | None        # 原始 chunk 对象（调试用）
```

---

## 6. 持久化

### PersistencePort (Protocol)

可插拔的持久化接口。业务系统实现此协议接入任意存储后端（PostgreSQL、MySQL、MongoDB 等）。

```python
@runtime_checkable
class PersistencePort(Protocol):
    async def save_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        agent_name: str,
        input_msg: Message,
        reply_msg: Message,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """保存一次 Agent 运行的最终结果。每次 engine.run() 结束时调用。"""

    async def save_messages(
        self, conversation_id: str, messages: list[Message]
    ) -> None:
        """批量保存会话消息。支持完整的消息历史覆盖写入。"""

    async def load_messages(
        self, conversation_id: str
    ) -> list[Message]:
        """加载会话的全部消息，按时间升序排列。run() 开始时调用。"""

    async def save_artifact(
        self, run_id: str, artifact_type: str, data: dict[str, Any]
    ) -> None:
        """保存工具生成的制品（如搜索结果、生成的文件等）。"""
```

注入方式：`AgentEngine(persistence=my_persistence)` 或 `AgentContext.persistence = my_persistence`。两者同时注入时优先使用 context 级别的。

---

## 7. 会话锁

### ConversationLockManager (Protocol)

会话级别的互斥锁协议。同一 `conversation_id` 的并发调用被串行化，防止消息写乱。

```python
@runtime_checkable
class ConversationLockManager(Protocol):
    def acquire(self, conversation_id: str) -> AsyncContextManager[None]:
        """
        获取会话锁。

        返回异步上下文管理器。在 async with lock.acquire("conv-123"): 块内
        独占该会话的读写权限。

        conversation_id 为空字符串时不加锁（bypass 模式）。
        """
```

### InMemoryConversationLockManager

单进程内存锁实现（默认）。基于 `asyncio.Lock`，懒惰创建，引用计数回收。

```python
class InMemoryConversationLockManager:
    def __init__(self): ...
    def acquire(self, conversation_id: str) -> AsyncContextManager[None]: ...

    @property
    def active_lock_count(self) -> int:
        """当前活跃锁数量（测试用）。"""
```

### RedisConversationLockManager

Redis 分布式锁实现。多副本部署必需。

```python
class RedisConversationLockManager:
    def __init__(self, redis_client: Any):
        """
        redis_client 需支持 execute_command() 方法。
        使用 SET NX PX 获取锁，compare-and-delete Lua 脚本释放锁。
        """

    def acquire(self, conversation_id: str) -> AsyncContextManager[None]: ...
```

---

## 8. 运行时事件

所有事件继承自 `RuntimeEvent`，通过 `to_dict()` 获取稳定的序列化形状。业务系统用这些事件构建 SSE、WebSocket 或审计日志。

### RuntimeEvent (基类)

```python
class RuntimeEvent:
    type: str          # 事件类型（如 "text_delta"），即 to_dict() 中的 event 字段
    run_id: str        # 运行 ID
    seq: int           # 事件序号（全局递增）
    timestamp: str     # ISO 8601 时间戳

    def to_dict(self) -> dict[str, Any]:
        """
        转为稳定的传输字典。形状：
        {
            "event": self.type,
            "data": { ... }  # 子类特有字段
        }
        """
```

### 事件类型详解

| 事件类型 | 类名 | 触发时机 | 关键字段 |
|---------|------|---------|---------|
| `run_started` | `RunStarted` | Agent 运行开始 | `agent_name`, `input_summary` |
| `run_completed` | `RunCompleted` | Agent 正常结束 | `result`, `exit_reason`, `elapsed_seconds` |
| `run_failed` | `RunFailed` | Agent 异常终止 | `error`, `error_code`, `elapsed_seconds` |
| `run_cancelled` | `RunCancelled` | 通过 interrupt() 取消 | `reason` |
| `turn_started` | `TurnStarted` | 每个 ReAct 轮次开始 | `turn` |
| `turn_ended` | `TurnEnded` | 每个 ReAct 轮次结束 | `turn`, `has_tool_calls`, `duration_ms` |
| `text_delta` | `TextDelta` | 模型输出文本增量 | `delta` |
| `reasoning_delta` | `ReasoningDelta` | 模型输出推理增量 | `delta` |
| `tool_call_started` | `ToolCallStarted` | 工具调用开始 | `tool`, `arguments` |
| `tool_call_completed` | `ToolCallCompleted` | 工具调用成功 | `tool`, `result`, `elapsed_seconds` |
| `tool_call_failed` | `ToolCallFailed` | 工具调用失败 | `tool`, `error`, `elapsed_seconds` |
| `tool_stream_event` | `ToolStreamEventEmitted` | 流式工具中间事件 | `tool`, `event_type`, `data` |
| `usage_report` | `UsageReport` | Token 用量统计 | `prompt_tokens`, `completion_tokens`, `total_tokens`, `duration_ms` |
| `approval_required` | `ApprovalRequired` | 破坏性工具需审批 | `tool`, `arguments`, `reason` |
| `todos_updated` | `TodosUpdated` | 任务清单变更 | `todos` |
| `user_question_asked` | `UserQuestionAsked` | Agent 向用户提问 | `question` |

### 消费方式

方式 A — 回调函数：

```python
async def on_event(event: RuntimeEvent):
    event_dict = event.to_dict()
    await websocket.send_json(event_dict)

answer = await engine.run(
    agent_name="assistant",
    query="你好",
    context=context,
    on_event=on_event,
)
```

方式 B — 流式队列（SseEventQueue）：

```python
context, event_stream = engine.create_streaming_context(
    request_id="req-1", query="查询订单", conversation_id="conv-1"
)
task = asyncio.create_task(engine.run(agent_name="assistant", query=context.query, context=context))

async for frame in event_stream:
    event_type = frame["event"]
    data = frame["data"]
    # 处理各类事件...
```

---

## 9. 错误系统

所有引擎异常继承自 `AgentEngineError`，提供结构化错误信息。

### 错误层次结构

```
Exception
 └── AgentEngineError               # 基础错误（code, message, category, retryable, status_code, details）
      ├── LLMError                  # LLM 相关错误基类
      │    ├── LLMHTTPError         # HTTP 错误（含 request body）
      │    │    └── LLMRateLimitError   # 速率限制 (HTTP 429)，retryable=True
      │    ├── LLMTimeoutError      # 请求超时，retryable=True
      │    ├── LLMConnectionError    # 连接失败，retryable=True
      │    ├── LLMStreamError       # 流式响应错误，retryable=True
      │    ├── ContextWindowExceededError  # 上下文窗口超限，retryable=False
      │    │    └── LLMContextWindowError   # 向后兼容别名
      │    └── UsageLimitReachedError # 用量达到上限，retryable=False
      ├── ToolExecutionError        # 工具执行失败，category="tool"
      └── RuntimeExecutionError     # 运行时错误，category="runtime"
           └── AgentCancelledError  # 运行被取消
```

### ErrorInfo 结构

```python
@dataclass
class ErrorInfo:
    code: str           # 错误码（如 "llm_rate_limit"）
    message: str        # 人类可读的错误消息
    category: str       # 错误分类（"agentengine", "llm", "tool", "runtime"）
    retryable: bool     # 是否可重试
    status_code: int | None  # HTTP 状态码（可选）
    details: dict[str, Any]  # 额外调试信息
```

### 实用工具

```python
def error_to_dict(error: BaseException) -> dict[str, Any]:
    """
    将任意异常转为可序列化的字典。
    AgentEngineError 子类 → 完整的 ErrorInfo 格式。
    其他异常 → {"code": "unexpected_error", "message": str(error), ...}
    """
```

---

## 10. Hook 系统

Hook 是 Agent 生命周期的拦截点。通过 `HookManager` 注册处理器，影响运行行为。

### HookEvent（拦截点）

```python
class HookEvent(enum.Enum):
    SESSION_START = "session_start"           # TurnRunner.run() 开始时
    USER_PROMPT_SUBMIT = "user_prompt_submit" # 用户输入即将进入记忆时
    PRE_TOOL_USE = "pre_tool_use"             # 工具执行前
    POST_TOOL_USE = "post_tool_use"           # 工具执行后
    AFTER_TURN = "after_turn"                 # 每轮 ReAct 结束后
    STOP = "stop"                             # 运行即将结束时（清理）
```

### HookOutcome / HookResult

```python
class HookOutcome(enum.Enum):
    SUCCESS = "success"               # 继续执行
    FAIL_CONTINUE = "fail_continue"   # 软失败，继续执行
    FAIL_ABORT = "fail_abort"         # 中止，抛出 HookAbortError
    STOP = "stop"                     # 本轮后正常结束循环

@dataclass
class HookResult:
    outcome: HookOutcome
    message: str = ""                 # 附加消息

    @classmethod
    def success(cls) -> HookResult: ...     # → outcome=SUCCESS
    @classmethod
    def fail_continue(cls, msg="") -> ...   # → outcome=FAIL_CONTINUE
    @classmethod
    def fail_abort(cls, msg="") -> ...      # → outcome=FAIL_ABORT
    @classmethod
    def stop(cls, msg="") -> ...            # → outcome=STOP
```

### HookManager

```python
class HookManager:
    def register(
        self, event: HookEvent, handler: HookFn, *, name: str | None = None
    ) -> None:
        """注册钩子处理器。按注册顺序依次执行。"""

    def on(self, event: HookEvent, *, name: str | None = None):
        """装饰器方式注册钩子：
        @manager.on(HookEvent.AFTER_TURN)
        async def my_hook(payload): return HookResult.success()
        """

    def unregister(self, event: HookEvent, handler: HookFn) -> None: ...
    def clear(self, event: HookEvent | None = None) -> None: ...
    def count(self, event: HookEvent) -> int: ...
    async def dispatch(self, event: HookEvent, payload) -> None: ...
        """
        按注册顺序执行所有匹配处理器。
        FAIL_ABORT → 停止调度，抛出 HookAbortError。
        STOP → 停止调度，不抛异常。
        """
```

注入方式：`context.extras["hooks"] = HookManager()`

### Hook Payload 类型

| HookEvent | Payload 类 | 关键字段 |
|-----------|-----------|---------|
| `SESSION_START` | `SessionStartPayload` | `agent_name`, `query_summary` |
| `USER_PROMPT_SUBMIT` | `UserPromptSubmitPayload` | `agent_name`, `query` |
| `PRE_TOOL_USE` | `PreToolUsePayload` | `tool_name`, `tool_call_id`, `arguments` |
| `POST_TOOL_USE` | `PostToolUsePayload` | `tool_name`, `ok`, `result_summary`, `elapsed_seconds` |
| `AFTER_TURN` | `AfterTurnPayload` | `agent_name`, `turn`, `has_tool_calls`, `final_answer` |
| `STOP` | `StopPayload` | `agent_name`, `status`, `elapsed_seconds` |

---

## 11. 企业中间件

`agentengine.enterprise` 包提供可选的横切关注点。所有中间件默认关闭，按需启用。

### MiddlewareChain

```python
class MiddlewareChain:
    def __init__(self, middlewares: list[MiddlewareFn]):
        """
        创建中间件链。middlewares 按洋葱模型执行：
        外层先执行前置逻辑，内层先执行后置逻辑。
        """

    async def run(
        self,
        config: RunConfig,
        context: AgentContext,
        query: str,
        inner_turn_fn,
    ) -> Any:
        """执行中间件链 + 内核 turn 函数。"""
```

### 可用中间件工厂

| 工厂函数 | 功能 | 来源模块 |
|---------|------|---------|
| `quota_middleware(store)` | 按租户/用户限流（次数、token） | `agentengine.enterprise.quota` |
| `approval_middleware(gate)` | 破坏性工具执行前等待人工确认 | `agentengine.enterprise.approval` |
| `otel_tracing_middleware()` | OpenTelemetry 分布式链路追踪 | `agentengine.enterprise.tracing` |
| `retry_middleware(config)` | LLM 调用失败指数退避重试 | `agentengine.enterprise.retry` |

用法示例：

```python
from agentengine.enterprise import MiddlewareChain, quota_middleware, QuotaStore

chain = MiddlewareChain([
    quota_middleware(QuotaStore()),
    # otel_tracing_middleware(),
    # retry_middleware(RetryConfig(max_retries=3)),
])

engine = AgentEngine(presets=presets, middleware=chain)
```

`agentengine.enterprise` 子模块还导出以下公开类型：

- **租户**: `TenantContext`
- **配额**: `QuotaStore`, `QuotaLimits`, `QuotaUsage`, `QuotaExceededError`
- **审批**: `ApprovalGate`, `ApprovalDecision`, `ApprovalResult`, `ApprovalDeniedError`
- **追踪**: `get_current_span`, `span_context`
- **重试**: `RetryConfig`
- **安全**: `SecretsProvider`, `EnvSecrets`, `SecretsError`

---

## 12. 取消与压缩

### CancellationToken

协程协作式取消信号。

```python
class CancellationToken:
    def __init__(self): ...

    @property
    def is_cancelled(self) -> bool:
        """是否已取消。"""

    def cancel(self, reason: str = "cancelled") -> None:
        """触发取消。设置事件并存储原因。"""

    async def wait(self) -> None:
        """阻塞等待直到取消。"""

    def throw_if_cancelled(self) -> None:
        """如果已取消，抛出 AgentCancelledError。"""
```

通过 `AgentEngine.interrupt(request_id)` 触发取消，内部 CancellationToken 被设置。

### Compactor (Protocol)

消息历史压缩协议。

```python
class Compactor(Protocol):
    async def compact(self, messages: list[Message]) -> list[Message]:
        """压缩消息列表。返回压缩后的消息。"""
```

### LLMSummaryCompactor

基于 LLM 的历史摘要压缩器。默认实现。

```python
class LLMSummaryCompactor:
    def __init__(
        self,
        llm: LLMClient,
        *,
        keep_recent: int = 8,
        # 保留最近 N 条消息不被摘要。

        max_input_chars: int = 60_000,
        # 输入转录用最大字符数。

        summary_prompt: str = "默认摘要提示词",
        # 指导 LLM 如何摘要的系统提示词。
    ): ...

    async def compact(self, messages: list[Message]) -> list[Message]:
        """
        压缩流程：
        1. 保留系统消息
        2. 将旧消息（含之前的摘要）交给 LLM 生成新摘要
        3. 返回 [系统消息, 新摘要消息, 最近 N 条消息]
        4. 若 LLM 返回空摘要，抛出 ContextWindowExceededError
        """
```

---

## 13. 状态与运行记录

### AgentState (Enum)

Agent 运行的生命周期状态。

```python
class AgentState(enum.Enum):
    IDLE = "idle"              # 初始状态，尚未开始执行
    RUNNING = "running"        # 正在执行 ReAct 循环
    FINISHED = "finished"      # 正常完成
    ERROR = "error"            # 异常终止
    CANCELLED = "cancelled"    # 被 interrupt() 取消
```

### AgentRun

单次 Agent 运行的运行时容器。持有配置、上下文、记忆和状态。

```python
@dataclass
class AgentRun:
    config: RunConfig              # 运行配置（不可变）
    context: AgentContext          # 请求上下文
    memory: Memory                 # 消息记忆（在 __post_init__ 中初始化）
    current_step: int = 0          # 当前步数
    state: AgentState = AgentState.IDLE  # 运行状态
```

### DEFAULT_AGENT_SYSTEM_PROMPT

引擎自动追加到每个 Agent 系统提示词末尾的安全指令常量：

```python
DEFAULT_AGENT_SYSTEM_PROMPT: str
# "You are an AI assistant. Continue working until the task is complete."
```

此常量通过 `_compose_system_prompt()` 拼接到 `AgentPreset.instructions` 之后。如果 `instructions` 为非空字符串，结果为 `"{instructions}\n\n{DEFAULT_AGENT_SYSTEM_PROMPT}"`；否则直接返回默认指令。

---

## 14. 流式输出

### SseEventQueue

`create_streaming_context()` 返回的异步队列。线程安全，支持多生产者单消费者。

```python
class SseEventQueue:
    async def put(self, frame: SseFrame) -> None:
        """放入原始帧。若队列已关闭则忽略。"""

    async def put_event(self, event: str, data: Any) -> None:
        """放入结构化事件帧。frame = {"event": event, "data": data}"""

    async def put_comment(self, comment: str) -> None:
        """放入 SSE 注释帧。"""

    async def close(self) -> None:
        """
        关闭队列。放入 None 哨兵通知消费者停止迭代。
        已关闭的队列会忽略后续 put 调用。
        """

    async def __aiter__(self) -> AsyncIterator[SseFrame]:
        """异步迭代。遇到 None 哨兵时结束。"""
```

### 流式消费模式

```python
# 模式 1：SseEventQueue（前台消费 + 后台运行）
context, stream = engine.create_streaming_context(
    request_id="req-1",
    query="用户问题",
    conversation_id="conv-1",
)
context.llm = my_llm

task = asyncio.create_task(
    engine.run(agent_name="assistant", query=context.query, context=context)
)

async for frame in stream:
    event_type = frame["event"]
    data = frame["data"]
    # → 实时推送给前端（SSE / WebSocket）

final_answer = await task  # → 完整字符串结果

# 模式 2：on_event 回调（引擎内部调用）
async def my_callback(event: RuntimeEvent):
    # 直接处理 RuntimeEvent 对象，不需要 SseEventQueue
    await ws.send(event.to_dict())

answer = await engine.run(
    agent_name="assistant",
    query="你好",
    context=context,
    on_event=my_callback,
)
# answer 仍是完整字符串，on_event 是旁路通知
```

## 15. 注意事项

- **不要** import `examples.reference_app` — 示范代码不属于 SDK，需要就复制到业务代码库改造
- **不要** 依赖 `agentengine.runtime.turn`、`agentengine.runtime.turn_runner`、`agentengine.base.context` 等内部模块 — 只用 `from agentengine import ...` 包根导入
- **不要** 在多副本生产环境用默认锁 — `InMemoryConversationLockManager` 只在单进程有效，必须换 Redis
- **不要** 把未脱敏的 query 直接入引擎 — PII 处理是业务系统责任
- **注意** SseEventQueue 帧的 `event` 字段名（如 `"text"`、`"done"`）与 `RuntimeEvent.type` 字段名（如 `"text_delta"`、`"run_completed"`）命名不同，这是两个层次的概念
