# skills — 技能扫描与调用

> `src/agentengine/skills/` 实现 Claude Code 风格的 Skill 系统：扫描磁盘上的 `SKILL.md` 文件，让 Agent 可以通过工具调用来加载和使用预定义的研究流程。

---

## 模块组成

```
skills/
├── loader.py    # SkillLoader + Skill dataclass
└── __init__.py
```

---

## Skill — 技能数据类

**文件：** `src/agentengine/skills/loader.py`

```python
@dataclass
class Skill:
    name: str
    description: str
    path: Path
    body: str  # SKILL.md body after frontmatter

    @cached_property
    def prompt(self) -> str:
        return f"Base directory: {self.path.parent}\n\n{self.body}"
```

---

## SkillLoader — 技能扫描器

**文件：** `src/agentengine/skills/loader.py`

### 设计意图

Skill 是「可复用的 Agent 工作流模板」。与硬编码在 Agent setup 中的工具不同，Skill：
- 由非开发者编写（产品经理、领域专家）
- 放在 `.agent/skills/` 目录下，独立于代码
- 通过 `SkillTool` 让 LLM 决定是否调用

### 扫描路径（优先级从高到低）

1. 项目级：`<cwd>/.agent/skills/`
2. 用户级：`~/.agent/skills/`

同名 Skill 以先扫描到的为准。

### 目录结构

```
.agent/skills/
├── python-review/
│   └── SKILL.md
├── data-analysis/
│   └── SKILL.md
└── api-testing/
    └── SKILL.md
```

### SKILL.md 格式

```markdown
---
name: python-review
description: Review Python code for style, bugs, and security issues.
---

# Instructions

When reviewing Python code, follow these steps:

1. Check for PEP 8 style violations
2. Look for common security issues (SQL injection, XSS, etc.)
3. Verify error handling is complete
4. Check for performance bottlenecks

Arguments: ${ARGUMENTS}
```

Frontmatter（`---` 包围的 YAML）必须包含 `name` 和 `description`。`body` 是 frontmatter 之后的内容，会在调用时作为 prompt 注入。

### API

```python
from agentengine.skills.loader import SkillLoader

loader = SkillLoader()

# 发现所有技能
skills = loader.discover()  # dict[str, Skill]

# 强制重新扫描
skills = loader.discover(force=True)

# 清空缓存
loader.invalidate()

# 创建新技能模板
path = loader.scaffold("my-skill", description="A new skill")
# 创建 .agent/skills/my-skill/SKILL.md
```

---

## SkillTool — 技能调用工具

**文件：** `src/agentengine/tools/builtin/skill_tool.py`

```python
class SkillTool(Tool):
    name = "Skill"
    description = "Execute a named skill. Call this FIRST before doing work a skill covers."
```

`SkillTool` 需要显式挂载到 `AgentContext.tool_collection`。核心 SDK 不会默认让所有 Agent 调用 Skill。

调用示例（LLM 发起）：

```json
{
  "tool": "Skill",
  "arguments": {
    "skill": "python-review",
    "args": "review src/main.py"
  }
}
```

SkillTool 会：
1. 查找对应 Skill
2. 将 `${ARGUMENTS}` 替换为传入的 args
3. 返回完整的指令文本，作为 tool result 写回 memory
4. LLM 下一轮就会按照 Skill 指令执行

---

## 使用场景

| 场景 | 做法 |
|------|------|
| 添加代码审查流程 | 创建 `.agent/skills/code-review/SKILL.md` |
| 让 deep_research 先查 Skill | system prompt 引导 "先调用 Skill 看是否有匹配流程" |
| 团队共享 Skill | 提交到仓库的 `.agent/skills/` 目录 |
| 个人私有 Skill | 放在 `~/.agent/skills/` |

---

## 关联文档

- [tools.md](tools.md) — SkillTool 属于内置工具
- [guides/create-agent.md](../guides/create-agent.md) — 在 Agent 中使用 Skill
