# tools — 工具系统与内置工具

> `src/agentengine/tools/` 是框架的工具层：抽象基类 `Tool`、集合管理 `ToolCollection`、注册工厂 `Registry`、统一执行器 `ToolExecutor`，以及丰富的内置工具集。

---

## 模块组成

```
tools/
├── base.py                      # Tool / StreamingTool / ToolStreamEvent
├── collection.py                # ToolCollection
├── registry.py                  # @register_tool / create_tool
├── executor.py                  # ToolExecutor — 统一执行入口
├── builtin/
│   ├── read_file_tool.py        # 读取文件（沙盒）
│   ├── bash_tool.py             # 执行 shell 命令
│   ├── skill_tool.py            # 调用 Skill
│   ├── file_write_tool.py       # 写入文件
│   ├── file_edit_tool.py        # 编辑文件
│   ├── glob_tool.py             # 文件 glob 搜索
│   ├── grep_tool.py             # 文本搜索
│   ├── todo_write_tool.py       # 任务清单管理
│   └── ask_user_question_tool.py # 向用户提问
└── __init__.py
```

---

## Tool — 抽象基类

**文件：** `src/agentengine/tools/base.py`

### 设计意图

Tool 是 Agent 的「手脚」。框架对工具执行提供统一治理（超时、截断、审批、配额），但工具本身只关心「接收参数、干活、返回结果」。

```python
class Tool(ABC):
    name: str
    description: str = ""
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    timeout_seconds: float = 30.0
    is_destructive: bool = False
    max_result_chars: int = 8000
    result_summary_strategy: Literal["truncate", "head_tail", "none"] = "truncate"

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }

    @abstractmethod
    async def run(self, **kwargs: Any) -> Any: ...
```

### 字段详解

| 字段 | 说明 |
|------|------|
| `name` | 工具唯一标识，LLM 通过这个名字调用 |
| `description` | 工具功能描述，LLM 靠它理解什么时候该用 |
| `schema` | JSON Schema，描述参数结构 |
| `timeout_seconds` | 框架强制超时时长 |
| `is_destructive` | 是否破坏性操作（触发审批门） |
| `max_result_chars` | 结果最大字符数，超限自动截断 |
| `result_summary_strategy` | 截断策略：`truncate` / `head_tail` / `none` |

---

## StreamingTool — 流式工具

**文件：** `src/agentengine/tools/base.py`

某些工具（如深度搜索）需要边执行边输出中间结果。`StreamingTool` 通过 async generator 实现：

```python
class StreamingTool(Tool):
    async def run_stream(self, **kwargs) -> AsyncGenerator[ToolStreamEvent, None]:
        # 执行过程中 yield 中间事件
        yield ToolStreamEvent(event_type="tool_thought", data="Searching...")
        yield ToolStreamEvent(event_type="search_result", data="Found 3 items")
        # 最后必须 yield 一个 is_final=True 的事件
        yield ToolStreamEvent(event_type="final_result", data="Summary", is_final=True)

    async def run(self, **kwargs) -> Any:
        # 默认实现：收集所有事件拼接成字符串
        ...
```

ToolExecutor 会自动识别 StreamingTool，调用 `run_stream()` 并转发中间事件到 Printer。

---

## ToolCollection — 工具集合

**文件：** `src/agentengine/tools/collection.py`

```python
class ToolCollection:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self.tool_map: dict[str, Tool] = {}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        self.tool_map[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tool_map.get(name)

    def require(self, name: str) -> Tool:
        ...  # 找不到时抛 KeyError

    def to_openai_tools(self) -> list[dict[str, Any]]:
        return [tool.to_openai_tool() for tool in self.tool_map.values()]
```

每个 `AgentContext` 自带一个空的 `ToolCollection`，在 Agent `setup()` 钩子中填充工具。

---

## Registry — 工具注册表

**文件：** `src/agentengine/tools/registry.py`

### 装饰器注册

```python
from agentengine.tools.registry import register_tool
from agentengine.tools.base import Tool

@register_tool("search")
class SearchTool(Tool):
    name = "search"
    ...
```

### 工厂创建

```python
from agentengine.tools.registry import create_tool

tool = create_tool("search")
```

注册表受 `RLock` 保护，线程安全。`registered_tools()` 返回副本，防止外部修改。

---

## ToolExecutor — 统一执行入口

**文件：** `src/agentengine/tools/executor.py`

### 职责

ToolExecutor 是工具调用的「统一关卡」，处理所有横切关注点：

```
ToolExecutor.execute(tool, arguments)
    │
    ├──► emit ToolCallStarted
    ├──► if tool.is_destructive: log WARNING
    ├──► asyncio.wait_for(tool.run(**args), timeout=timeout)
    │       ├──► 成功
    │       │      ├──► _summarize(result, tool)  # 截断大结果
    │       │      └──► emit ToolCallCompleted
    │       └──► 失败/超时
    │              └──► emit ToolCallFailed
    └──► return ToolExecutionResult
```

### ToolExecutionResult

```python
@dataclass(slots=True)
class ToolExecutionResult:
    tool_name: str
    ok: bool
    content: str           # 给 LLM 看的结果摘要
    raw: Any | None = None # 原始返回值
    error: str | None = None
    truncated: bool = False
    elapsed_seconds: float = 0.0
    tool_call_id: str = ""
```

### 结果截断策略

| strategy | 行为 |
|----------|------|
| `truncate` | 直接截断到 `max_result_chars`，末尾加 `...[truncated]` |
| `head_tail` | 保留头部和尾部各一半，中间加 `...[truncated]...` |
| `none` | 不截断，可能超长 |

---

## 内置工具详解

### ReadFileTool — 安全文件读取

```python
class ReadFileTool(Tool):
    name = "read_file"
    timeout_seconds = 5.0
    max_result_chars = 8000
    result_summary_strategy = "head_tail"
```

特性：
- **沙盒**：只能读取 `workspace_root` 下的文件
- **大小限制**：默认最多读取 8192 字节
- **UTF-8 解码**：非法字符用 replacement 字符替换

### BashTool — Shell 命令执行

```python
class BashTool(Tool):
    name = "bash"
    timeout_seconds = 30.0
    max_result_chars = 16000
```

特性：
- **跨平台**：POSIX 用 `/bin/sh -c`，Windows 用 `cmd /c`
- **可覆盖 shell**：支持 `sh`, `bash`, `cmd`, `powershell`, `pwsh`
- **破坏性检测**：自动识别 `rm`, `git push --force` 等危险命令并打 WARNING
- **输出截断**：stdout 和 stderr 分别限制，超限用 head-tail 截断

### SkillTool — 调用预定义技能

```python
class SkillTool(Tool):
    name = "Skill"
    description = "Execute a named skill. Call this FIRST before doing work a skill covers."
```

接收 `skill` 名称和可选 `args`，从 `SkillLoader` 查找对应 `SKILL.md`，将指令注入对话上下文。

### FileWriteTool / FileEditTool

- `file_write`：写入文件内容
- `file_edit`：基于行号或搜索替换编辑文件

两者都受 `TurnFileAccessTracker` 保护：edit 工具拒绝覆盖未被 read 过的文件，防止意外破坏。

### GlobTool / GrepTool

- `glob`：按模式搜索文件路径（如 `src/**/*.py`）
- `grep`：按正则搜索文件内容

### TodoWriteTool

管理当前会话的任务清单：
- `status`: `pending` | `in_progress` | `completed`
- 每次更新会 emit `TodosUpdated` 事件
- 全部完成后自动清空存储

### AskUserQuestionTool

Agent 中途向用户发起澄清问题：
- 不阻塞运行，立即返回占位符
- emit `UserQuestionAsked` 事件
- 用户答复作为下一轮 user message 流入

---

## 完整示例：创建并注册自定义工具

```python
# my_tools/calculator.py
from agentengine.tools.base import Tool
from agentengine.tools.registry import register_tool

@register_tool("calculator")
class CalculatorTool(Tool):
    name = "calculator"
    description = "Perform basic arithmetic operations."
    schema = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Math expression to evaluate, e.g. '2 + 3 * 4'"
            }
        },
        "required": ["expression"]
    }
    timeout_seconds = 5.0
    is_destructive = False

    async def run(self, **kwargs) -> str:
        expression = kwargs.get("expression", "")
        try:
            # 安全计算：仅允许数字和运算符
            allowed = set("0123456789+-*/.() ")
            if not all(c in allowed for c in expression):
                return "Error: invalid characters in expression"
            result = eval(expression)  # 实际项目中用 safer_eval
            return str(result)
        except Exception as e:
            return f"Error: {e}"
```

接入 Agent：

```python
from agentengine.base.context import AgentContext
from agentengine.tools.registry import create_tool

async def setup(context: AgentContext) -> None:
    context.tool_collection.add(create_tool("calculator"))
```

---

## 工具治理检查清单

当你写一个新工具时，确认：

| 检查项 | 建议 |
|--------|------|
| `timeout_seconds` | 设置合理的超时，防止挂起 |
| `max_result_chars` | 限制输出大小，防止爆 Memory |
| `is_destructive` | 会修改数据的工具设为 `True`，触发审批 |
| `schema` | 用清晰的 JSON Schema，帮助 LLM 正确传参 |
| `description` | 写清楚什么时候该用，减少 LLM 误调用 |
| 异常处理 | run() 内部捕获异常，返回错误字符串而非抛异常 |

---

## 关联文档

- [runtime.md](runtime.md) — run_turn 如何调用 ToolExecutor

## ExecPolicy

`ExecPolicy` is evaluated by `ToolExecutor` before a tool runs. Prefix rules can
`allow`, `deny`, or `ask`. `deny` returns a failed tool result to the model;
`ask` emits `ApprovalRequired` and returns a failed tool result until the host
approval flow handles the request.
- [base.md](base.md) — AgentContext 中的 ToolCollection
- [skills.md](skills.md) — SkillTool 和 SkillLoader
- [guides/create-tool.md](../guides/create-tool.md) — 更详细的工具开发指南
