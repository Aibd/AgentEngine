# memory — 消息与记忆管理

> `src/agentengine/memory/` 提供对话消息的抽象 `Message` 和带自动裁剪的有序消息存储 `Memory`。

---

## 模块组成

```
memory/
├── message.py    # Message dataclass + Role enum
├── memory.py     # Memory — 有序存储 + 自动裁剪 + 持久化
└── __init__.py
```

---

## Role — 消息角色枚举

**文件：** `src/agentengine/memory/message.py`

```python
class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
```

对应 OpenAI Chat Completions API 的四种角色。

---

## Message — 单条消息

**文件：** `src/agentengine/memory/message.py`

### 设计意图

Message 是框架与 LLM 之间的「通用语言」。它同时支持：
- 标准文本对话
- 多模态输入（base64 图片）
- 工具调用（OpenAI 原始格式）
- 推理链保留（DeepSeek `reasoning_content`）

```python
@dataclass(slots=True)
class Message:
    role: Role
    content: str = ""
    reasoning_content: str = ""              # 模型推理过程
    name: str | None = None                  # function call 名称
    tool_call_id: str | None = None          # 工具结果对应的调用 ID
    tool_calls: list[dict[str, Any]] | None = None  # 助手消息中的工具调用
    base64_image: str | None = None          # 用户图片输入
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 与 OpenAI 格式的互转

```python
# Message → OpenAI dict
msg.to_openai()  # {"role": "assistant", "content": "...", "tool_calls": [...]}

# OpenAI dict → Message
Message.from_openai({"role": "user", "content": "hello"})
```

**多模态支持：** 当 `base64_image` 存在且 `role == USER` 时，`to_openai()` 输出 OpenAI 视觉格式：

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "描述这张图片"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ..."}}
  ]
}
```

### 工厂方法

```python
Message.system("You are a helpful assistant.")
Message.user("Hello", base64_image="/9j/4AAQ...")
Message.assistant("Hi there!")
Message.assistant(tool_calls=[{"id": "call_1", "function": {"name": "read_file", "arguments": "{}"}}])
Message.tool("File content here", tool_call_id="call_1")
```

---

## Memory — 有序消息存储

**文件：** `src/agentengine/memory/memory.py`

### 设计意图

Memory 是 Agent 的「短期记忆」。它维护一个按时间顺序的消息列表，并在超出限制时自动裁剪，**始终保留系统消息**。

```python
@dataclass
class Memory:
    messages: list[Message] = field(default_factory=list)
    max_messages: int = 0       # 消息数上限（0 = 不限制）
    max_tokens: int = 0         # Token 数上限（0 = 不限制）
    _lock: Any = field(default_factory=RLock, init=False, repr=False)
```

### 线程安全

所有修改操作（`append`, `extend`, `clear`）都受 `RLock` 保护。`snapshot()` 返回当前列表的副本，保证并发读取安全。

### 自动裁剪策略

```
Memory 收到新消息
    │
    ▼
┌─────────────────────────────────────────┐
│ 1. 分离 system / non-system 消息        │
│    kept_system = [所有 system 消息]       │
│    non_system  = [其他消息]               │
│                                         │
│ 2. 应用消息数限制                        │
│    if max_messages > 0:                  │
│        budget = max_messages - len(kept_system)
│        non_system = non_system[-budget:] │
│                                         │
│ 3. 应用 Token 限制                       │
│    if max_tokens > 0:                    │
│        从 newest → oldest 遍历           │
│        累加 token 直到达到上限            │
│        non_system = 保留下来的部分         │
│                                         │
│ 4. 合并                                  │
│    messages = kept_system + non_system   │
└─────────────────────────────────────────┘
```

**关键规则：**
- 系统消息**永远保留**，不计入裁剪预算
- 消息数限制和 Token 限制**同时生效**，取更严格的那个
- 裁剪时**保留最新的消息**，丢弃最旧的
- Token 估算采用启发式：混合 CJK/ASCII 文本按 ~2 字符/token

### 便捷方法

```python
memory.add_system_message("You are a coder.")
memory.add_user_message("Write a hello world.")
memory.add_assistant_message("Here you go: ...")
memory.add_tool_message("File content", tool_call_id="call_1")

# 查询
memory.last_user_message()       # "Write a hello world."
memory.last_assistant_message()  # "Here you go: ..."
memory.estimated_tokens()        # 42
memory.to_openai()               # 转成 OpenAI API 格式
memory.snapshot()                # 返回消息列表的安全副本
```

### 持久化

Memory 通过 `PersistencePort` 与数据库交互：

```python
# 加载历史对话（run_turn 开始时自动调用）
await memory.load_from_db(persistence, conversation_id="conv-123")

# 保存对话（run_turn 结束时自动调用）
await memory.save_to_db(persistence, conversation_id="conv-123")
```

`load_from_db` 的加载策略：
- 不清空已有消息
- 加载的消息**插入到 system 消息之后、现有消息之前**
- 这样可保证：system 提示词在最前面，历史对话在中间，当前请求的消息在最后

---

## 记忆管理最佳实践

| 场景 | 建议 |
|------|------|
| 普通聊天 | `max_messages=20` 防止无限增长 |
| 长文档分析 | `max_messages=0, max_tokens=8000` 按 Token 裁剪 |
| 多轮工具调用 | `max_messages=30` 配合 auto-compaction 给工具调用留空间 |
| 保留完整上下文 | `max_messages=0, max_tokens=0`（不裁剪，注意爆窗） |

---

## 代码示例

```python
from agentengine.memory.memory import Memory
from agentengine.memory.message import Message

memory = Memory(max_messages=5)

memory.add_system_message("You are a helpful assistant.")
memory.add_user_message("What is Python?")
memory.add_assistant_message("Python is a programming language.")
memory.add_user_message("What is JavaScript?")
memory.add_assistant_message("JavaScript is a web programming language.")
memory.add_user_message("What is Rust?")
memory.add_assistant_message("Rust is a systems programming language.")
memory.add_user_message("What is Go?")  # 第 6 条非系统消息

# 因为 max_messages=5，系统消息保留，最旧的用户/助手消息被丢弃
print(len(memory.messages))  # 5（1 system + 4 non-system）
```

---

## 关联文档

- [base.md](base.md) — AgentRun 持有 Memory 实例
- [llm.md](llm.md) — LLMClient 接收 `list[Message]`
- [persistence.md](persistence.md) — PersistencePort 接口
