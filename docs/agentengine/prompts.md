# prompts — 提示词加载器

> `src/agentengine/prompts/` 提供基于 YAML 的提示词模板管理，支持缓存、热刷新和多文件组织。

---

## 模块组成

```
prompts/
├── loader.py    # PromptLoader
└── __init__.py
```

---

## PromptLoader

**文件：** `src/agentengine/prompts/loader.py`

### 设计意图

将提示词从代码中分离，方便：
- 非开发者修改提示词
- 多语言/多场景提示词切换
- A/B 测试不同提示词效果
- 版本控制提示词变更

### 支持的 YAML 格式

```yaml
# prompts/general_chat.yaml
version: 1
system: |
  You are a helpful assistant. Answer in Chinese.
next_step: |
  Continue the conversation naturally.
templates:
  greeting: "Hello, {name}!"
  farewell: "Goodbye, {name}!"
```

### API

```python
from agentengine.prompts.loader import PromptLoader

loader = PromptLoader(root="prompts")

# 加载完整 YAML
data = loader.load("general_chat.yaml")

# 便捷方法
system = loader.get_system_prompt("general_chat")      # "You are a helpful assistant..."
next_step = loader.get_next_step_prompt("general_chat") # "Continue the conversation..."
greeting = loader.get_template("general_chat", "greeting")  # "Hello, {name}!"

# 强制刷新缓存
data = loader.load("general_chat.yaml", refresh=True)

# 清空缓存
loader.clear_cache()
```

### 缓存机制

- 第一次 `load()` 读取磁盘并缓存到内存字典
- 后续调用直接返回缓存（除非 `refresh=True`）
- 扩展名自动推断：`.yaml` → `.yml` → 无扩展名
- 文件不存在返回 `{}`，不抛异常

### 无 YAML 依赖的降级

如果环境中没有 `PyYAML`，loader 会将文件内容作为 `{"raw": text}` 返回，保证框架不硬崩。

---

## 在 AgentSpec 中使用

```python
from agentengine.prompts.loader import PromptLoader
from agentengine.spec import AgentSpec

loader = PromptLoader(root="prompts")

SPEC = AgentSpec(
    name="general_chat",
    system_prompt=loader.get_system_prompt("general_chat"),
    next_step_prompt=loader.get_next_step_prompt("general_chat"),
)
```

---

## 关联文档

- [base.md](base.md) — AgentSpec 的 system_prompt / next_step_prompt 字段
