# 使用 AgentEngine Skills

本文档面向 Skill 作者和终端用户，说明如何编写、安装和使用 Skill。

## 1. 什么是 Skill

Skill 是一个可复用的任务模板，用 Markdown 描述Agent 在遇到某类任务时应该如何执行。一个 skill 可以包含：

- `SKILL.md`：元数据和执行指令（必需）
- `scripts/`：可执行脚本（可选）
- `references/`：参考文档（可选）
- `assets/`：模板、数据文件等静态资源（可选）

## 2. 第一个 Skill

以"生成标准化 commit message"为例，创建一个 `git-commit` skill：

```bash
mkdir -p .agent/skills/git-commit
```

创建 `.agent/skills/git-commit/SKILL.md`：

```markdown
---
name: git-commit
description: 根据 staged changes 生成规范化 commit message。当用户要求提交代码时使用。
---

# Git Commit Skill

1. 执行 `git diff --cached` 查看已暂存的变更内容。
2. 分析变更，生成一个 Conventional Commits 格式的 message：
   - type(feat/fix/refactor/docs/test/chore)
   - scope（可选，标明影响模块）
   - subject（简洁描述，中文）
3. 将 message 输出给用户确认，不要直接执行 git commit。

Arguments: ${ARGUMENTS}
```

## 3. 带脚本的可执行 Skill

```text
.agents/skills/data-analysis/
├── SKILL.md
├── scripts/
│   └── summarize.py
└── references/
    └── format.md
```

`SKILL.md`：

```markdown
---
name: data-analysis
description: Analyze CSV files and produce a summary report.
---

# Data Analysis

1. Read the input CSV file path from the arguments.
2. Run the analysis script:
   ```bash
   python3 scripts/summarize.py "$INPUT_FILE"
   ```
3. Use the script output to write a concise report.

Arguments: ${ARGUMENTS}
```

`scripts/summarize.py`：

```python
import sys
import csv
from pathlib import Path

input_file = Path(sys.argv[1])
rows = list(csv.reader(input_file.read_text().splitlines()))
print(f"Rows: {len(rows)}")
print(f"Columns: {len(rows[0]) if rows else 0}")
```

## 4. 在 Agent Definition 中启用 Skill

### Markdown definition

```markdown
---
name: data_analyst
tools: [read_file, Skill, ReadSkillResource, RunSkillScript]
---
You are a data analyst.
```

### Python definition

```python
from agentengine import AgentDefinition, AgentContext
from agentengine.skills.loader import SkillLoader
from agentengine.tools.builtin import SkillTool, ReadSkillResource, RunSkillScript

async def _setup(context: AgentContext) -> None:
    loader = SkillLoader()
    context.tool_collection.add(SkillTool(loader))
    context.tool_collection.add(ReadSkillResource(loader))
    # RunSkillScript requires a SandboxManager; omit if sandbox is unavailable.

definition = AgentDefinition(
    name="data_analyst",
    instructions="You are a data analyst.",
    setup=_setup,
)
```

## 5. 模型如何与 Skill 交互

1. **发现**：会话开始时，模型看到可用 skills 列表。
2. **激活**：任务匹配 skill 描述时，模型调用 `Skill(skill="data-analysis")`。
3. **读取资源**：需要参考文档时，调用 `ReadSkillResource(skill="data-analysis", path="references/format.md")`。
4. **执行脚本**：需要运行脚本时，调用 `RunSkillScript(skill="data-analysis", script="scripts/summarize.py", args=["data.csv"])`。

## 6. 安装和分享 Skill

### 通过 Web UI

前端 Skills 管理页面支持上传 zip 包、启用/禁用、删除。

### 通过文件系统

把 skill 目录复制到项目级或用户级目录：

```text
project/.agents/skills/<skill-name>/
~/.agents/skills/<skill-name>/
```

### 打包 zip

```bash
cd .agents/skills/data-analysis
zip -r ../../../data-analysis.zip .
```

## 7. 最佳实践

- **保持 `SKILL.md` 简洁**：建议不超过 500 行，详细参考移入 `references/`。
- **脚本自包含**：使用 `uv run`、`pipx`、`npx` 等工具声明依赖，或在脚本内联声明。
- **避免交互式提示**：脚本必须是非交互的，所有输入通过参数或环境变量传入。
- **输出结构化数据**：优先输出 JSON/CSV，方便模型消费。
- **路径使用相对路径**：脚本中引用资源时使用相对 skill 目录根的路径，如 `scripts/run.sh`、`references/format.md`。
- **名称与目录一致**：frontmatter `name` 建议与父目录名一致，避免警告。
