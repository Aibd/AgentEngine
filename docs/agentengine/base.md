# base — Agent 的声明与运行时

> `src/agentengine/base/` 包含 Agent 的四个核心抽象：AgentSpec（声明）、AgentRun（运行时）、AgentContext（依赖上下文）、AgentState（状态机）。

---

## 模块定位

```
┌─────────────────────────────────────────┐
│              base/                        │
│  ┌─────────┐ ┌─────────┐ ┌────────────┐ │
│  │AgentSpec│ │AgentRun │ │AgentContext│ │
│  │(frozen) │ │(mutable)│ │(dependencies)│
│  └─────────┘ └─────────┘ └────────────┘ │
│  ┌─────────┐                             │
│  │AgentState│                            │
│  │ (enum)   │                            │
│  └─────────┘                             │
└─────────────────────────────────────────┘
```

这是整个框架的「元数据层」。所有其他模块（runtime、llm、tools、stream）都围绕这四个抽象工作。

---

## AgentSpec — 声明「我是谁」

**文件：** `src/agentengine/spec.py`

### 设计意图

AgentSpec 将「身份配置」提取为不可变的 frozen dataclass，让 Agent 的定义变成**纯数据**。循环逻辑、工具注册、提示词生成各自独立，不耦合在 Agent 类中。

```python
@dataclass(frozen=True, slots=True)
class AgentSpec:
    name: str
    system_prompt: str = ""
    next_step_prompt: str = ""
    description: str = ""
    max_steps: int = 10
    max_messages: int = 0          # 0 = 不限制
    setup: SetupHook | None = None
    teardown: SetupHook | None = None
    extras: dict[str, object] = field(default_factory=dict)
```

### 字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | Agent 唯一标识，用于 REGISTRY 查找 |
| `system_prompt` | `str` | 系统提示词，run_turn 开始时注入 Memory |
| `next_step_prompt` | `str` | 每轮 ReAct 循环插入到最后一条用户消息前的指导语 |
| `description` | `str` | 人类可读描述 |
| `max_steps` | `int` | ReAct 循环最大轮数，默认 10 |
| `max_messages` | `int` | Memory 消息数上限，0 表示不限制 |
| `setup` | `SetupHook` | 异步钩子，`run_turn` 开始时调用，用于注册工具 |
| `teardown` | `SetupHook` | 异步钩子，`run_turn` 结束时调用，用于释放资源 |
| `extras` | `dict` | 扩展字段，供业务层自由使用 |

### 为什么 frozen？

- **线程安全**：多请求共享同一个 Spec 实例不会互相污染
- **可哈希**：可以作为字典 key、可以放入缓存
- **显式覆盖**：运行时想改 `max_steps`？用 `dataclasses.replace(spec, max_steps=5)`，而不是偷偷 mutation

### 验证规则

`__post_init__` 自动校验：
- `name` 不能为空
- `max_steps >= 1`
- `max_messages >= 0`

---

## AgentRun — 运行时「现在在哪」

**文件：** `src/agentengine/base/agent.py`

### 设计意图

如果 AgentSpec 是「蓝图」，AgentRun 就是「施工现场」。它持有每次请求独有的可变状态：记忆、步数、生命周期状态。

```python
@dataclass(slots=True)
class AgentRun:
    spec: AgentSpec
    context: AgentContext
    memory: Memory = field(init=False)
    current_step: int = 0
    state: AgentState = AgentState.IDLE
```

### 生命周期

```
AgentRun 创建
    │
    ▼
IDLE ──setup()──► RUNNING ──循环中──► FINISHED / ERROR / CANCELLED
    │                                         │
    │                                     teardown()
    │                                         │
    └─────────────────────────────────────────┘
```

### 委托模式

AgentRun 自己不实现逻辑，而是**委托**给 Spec：

```python
@property
def name(self) -> str:
    return self.spec.name

@property
def max_steps(self) -> int:
    return self.spec.max_steps

def system_prompt(self) -> str:
    return self.spec.system_prompt

async def setup(self) -> None:
    if self.spec.setup is not None:
        await self.spec.setup(self.context)
```

这意味着：改 Agent 行为 → 改 Spec；不改 AgentRun 类。

---

## AgentContext — 依赖上下文

**文件：** `src/agentengine/base/context.py`

### 设计意图

一次运行所需的所有「外部依赖」：LLM、Printer、工具集合、会话信息、持久化接口……全部装在一个 dataclass 里，避免函数签名膨胀。

```python
@dataclass
class AgentContext:
    request_id: str
    query: str
    llm: LLMClient | None = None
    printer: Printer | None = None
    tool_collection: ToolCollection = field(default_factory=ToolCollection)
    session_id: str = ""
    conversation_id: str = ""
    user: Any = None
    db: Any = None
    persistence: PersistencePort | None = None
    extras: dict[str, Any] = field(default_factory=dict)
```

### 自动注册 SkillTool

`__post_init__` 会自动检查 tool_collection 是否已有 "Skill" 工具，如果没有，就创建一个 `SkillTool` 加进去。这保证了**每个 Agent 默认都有 Skill 能力**。

```python
def __post_init__(self) -> None:
    if not any(t.name == "Skill" for t in self.tool_collection.tool_map.values()):
        loader = self.extras.get("skill_loader") or SkillLoader()
        self.tool_collection.add(SkillTool(loader))
```

### extras 的用途

`extras` 是框架与业务层之间的「秘密通道」：

| 键 | 用途 | 谁写入 |
|----|------|--------|
| `run_id` / `turn_id` | TurnRunner 生成的标识 | TurnRunner |
| `hooks` | HookManager 实例 | 业务层或 TurnRunner |
| `approval_gate` | ApprovalGate 实例 | 企业中间件 |
| `tenant` | TenantContext 实例 | 租户中间件 |
| `_quota_store` / `_quota_tenant_id` | 配额存储 | QuotaMiddleware |
| `file_access_tracker` | 文件访问追踪器 | TurnRunner |
| `workspace_root` | 工作区根目录 | 业务层 |

---

## AgentState — 生命周期状态机

**文件：** `src/agentengine/base/state.py`

```python
class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    FINISHED = "finished"
    ERROR = "error"
    CANCELLED = "cancelled"
```

### 状态转换表

| 从 \ 到 | IDLE | RUNNING | FINISHED | ERROR | CANCELLED |
|---------|------|---------|----------|-------|-----------|
| **IDLE** | — | setup() 成功 | — | — | — |
| **RUNNING** | — | — | 正常结束 | 异常 | CancelledError |
| **FINISHED** | — | — | — | — | — |
| **ERROR** | — | — | — | — | — |
| **CANCELLED** | — | — | — | — | — |

FINISHED / ERROR / CANCELLED 都是终态，不可再转换。

---

## 完整示例：定义一个带工具的 Agent

```python
# src/agents/coder/spec.py
from agentengine.base.context import AgentContext
from agentengine.spec import AgentSpec
from agentengine.tools.builtin.bash_tool import BashTool
from agentengine.tools.builtin.read_file_tool import ReadFileTool

_SYSTEM_PROMPT = """你是一个代码助手。可以读取文件和执行 shell 命令来帮助用户。
注意：
- 读取文件前先确认路径存在
- 执行命令前说明你要做什么"""

async def _setup(context: AgentContext) -> None:
    context.tool_collection.add(ReadFileTool())
    context.tool_collection.add(BashTool())

SPEC = AgentSpec(
    name="coder",
    system_prompt=_SYSTEM_PROMPT,
    max_steps=8,
    setup=_setup,
)
```

```python
# src/agents/__init__.py
from agents.coder.spec import SPEC as CODER_SPEC

REGISTRY = {
    "general_chat": GENERAL_CHAT_SPEC,
    "deep_research": DEEP_RESEARCH_SPEC,
    "coder": CODER_SPEC,  # 新增
}
```

---

## 关联文档

- [runtime.md](runtime.md) — TurnRunner 如何使用 AgentRun
- [tools.md](tools.md) — ToolCollection 和工具注册
- [stream.md](stream.md) — Printer 如何消费事件
