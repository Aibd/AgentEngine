# AgentEngine Skill 系统设计

本文档面向开发者和宿主系统集成者，说明 AgentEngine 中 Skill 系统的设计原理、数据契约和安全边界。

## 1. 设计目标

Skill 系统是 AgentEngine 对 [Agent Skills](https://agentskills.io/) 开放的实现：

- **能力包化**：一个 skill 是包含 `SKILL.md` + `scripts/` + `references/` + `assets/` 的目录。
- **渐进式披露（Progressive Disclosure）**：模型先看到 catalog，激活后看到完整指令，执行时按需读取资源。
- **可执行**：skill 脚本在 Sandbox 中运行，而不是通过宿主机 BashTool。
- **上下文持久**：激活后的 skill 指令在 trim、compaction 和持久化后仍然有效。

## 2. 数据契约

### 2.1 Skill 目录结构

```text
.agents/skills/example/
├── SKILL.md
├── scripts/
│   └── analyze.py
├── references/
│   └── FORMAT.md
└── assets/
    └── template.csv
```

### 2.2 `SKILL.md` frontmatter

| 字段 | 必需 | 说明 |
|---|---|---|
| `name` | 是 | skill 标识名，建议与目录名一致 |
| `description` | 是 | 一句话描述，用于 catalog |
| `license` | 否 | 许可证 |
| `compatibility` | 否 | 环境要求 |
| `metadata` | 否 | 额外元数据 |

### 2.3 `Skill` 对象

```python
@dataclass
class Skill:
    name: str
    description: str
    path: Path
    body: str
    base_dir: Path | None
    resources: SkillResources | None
```

- `body` 是 frontmatter 之后的 Markdown 正文。
- `resources` 包含三类资源的 POSIX 相对路径元组。
- `activation_content(args)` 生成标准 XML 激活块。

## 3. Progressive Disclosure

### 3.1 Catalog 层

在会话开始时，把 enabled skills 的 `name` 和 `description` 暴露给模型：

```xml
<available_skills>
  <skill>
    <name>data-analysis</name>
    <description>Analyze structured files and produce reports.</description>
  </skill>
</available_skills>
```

暴露方式二选一：
- **SkillTool.description + schema enum**（默认）：限制模型只能调用 enabled skills。
- **system prompt 注入**：通过 `AgentEngine(enable_skill_catalog=True, skill_loader=...)` 开启。

### 3.2 Instructions 层

模型调用 `Skill(skill="data-analysis")` 后，返回：

```xml
<skill_content name="data-analysis" description="...">
  <skill_directory>/path/to/.agents/skills/data-analysis</skill_directory>
  <instructions>...</instructions>
  <skill_resources>
    <scripts>
      <file>scripts/analyze.py</file>
    </scripts>
    <references/>
    <assets/>
  </skill_resources>
</skill_content>
```

### 3.3 Resources 层

模型通过 `ReadSkillResource(skill=..., path=...)` 按需读取资源。

## 4. 安全边界

### 4.1 路径安全

- 所有资源路径必须是 POSIX 相对路径。
- 拒绝空路径、绝对路径、`..`、解析后逃逸 skill 根目录的 symlink。
- 资源读取和脚本执行只能访问 `Skill.resources` 中声明的文件。

### 4.2 脚本执行安全

- skill 脚本**不得**通过宿主机 `BashTool` 执行。
- `RunSkillScript` 必须配合 `SandboxManager` 使用。
- skill 包先通过 `SkillMaterializer` 复制到会话 workspace 的 `.skills/<name>/`。
- `SessionSandbox.exec_argv()` 以 argv 数组执行脚本，避免 shell 注入。
- Sandbox 不可用时明确拒绝，不回退宿主机执行。

### 4.3 上下文保护

- `SkillTool` 返回 `ToolResult(content=..., metadata={"skill_activation": True, "skill_name": ...})`。
- `ToolExecutor` 把 metadata 传入 `Message.tool()`。
- `Memory._trim()` 识别 skill 激活消息对，原子保留 assistant tool call + tool result。
- `LLMSummaryCompactor` 在压缩前提取 protected pairs，压缩后插回。
- `Message.to_persistent()` 保证 metadata 被持久化，重启后保护仍然有效。

## 5. 生命周期

```text
Discovery (SkillLoader)
    ↓
Catalog (SkillTool.description / system prompt)
    ↓
Activation (SkillTool.run → Skill.activation_content)
    ↓
Resource Read (ReadSkillResource)
    ↓
Script Execution (RunSkillScript → SkillMaterializer → Sandbox.exec_argv)
```

## 6. 路径优先级

默认扫描顺序（高优先级优先）：

1. `project/.agents/skills`
2. `project/.agent/skills`
3. `user/.agents/skills`
4. `user/.agent/skills`

同名 skill 由先扫描到的高优先级目录覆盖。
