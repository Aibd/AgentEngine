# 指南：创建新 Agent

> 从复制 `general_chat` 到注册上线，手把手教你添加一个自定义 Agent。

---

## 步骤 1：创建 spec 文件

```bash
mkdir -p src/agents/my_agent
```

创建 `src/agents/my_agent/spec.py`：

```python
from agentengine.base.context import AgentContext
from agentengine.spec import AgentSpec
from agentengine.tools.builtin.read_file_tool import ReadFileTool

_SYSTEM_PROMPT = """你是一个 Python 代码审查专家。

你的职责：
1. 检查代码是否符合 PEP 8 规范
2. 发现潜在的 bug 和安全漏洞
3. 指出性能瓶颈
4. 给出具体的改进建议

审查时要引用具体代码行，不要泛泛而谈。"""

async def _setup(context: AgentContext) -> None:
    # 注册工具
    context.tool_collection.add(ReadFileTool())

SPEC = AgentSpec(
    name="code_reviewer",
    description="Python code review agent",
    system_prompt=_SYSTEM_PROMPT,
    max_steps=5,
    setup=_setup,
)
```

---

## 步骤 2：注册到 REGISTRY

编辑 `src/agents/__init__.py`：

```python
from agents.deep_research.spec import SPEC as DEEP_RESEARCH_SPEC
from agents.general_chat.spec import SPEC as GENERAL_CHAT_SPEC
from agents.my_agent.spec import SPEC as CODE_REVIEWER_SPEC  # 新增

REGISTRY: dict[str, AgentSpec] = {
    "general_chat": GENERAL_CHAT_SPEC,
    "deep_research": DEEP_RESEARCH_SPEC,
    "code_reviewer": CODE_REVIEWER_SPEC,  # 新增
}
```

---

## 步骤 3：运行测试

```bash
PYTHONIOENCODING=utf-8 uv run python scripts/chat_pretty.py code_reviewer "审查这段代码：print('hello')"
```

---

## 高级：使用 PromptLoader

如果提示词很长，可以放到 YAML 文件：

```yaml
# prompts/code_reviewer.yaml
version: 1
system: |
  你是一个 Python 代码审查专家...
next_step: |
  继续审查下一个文件，或者给出最终总结报告。
```

```python
from agentengine.prompts.loader import PromptLoader

loader = PromptLoader(root="prompts")

SPEC = AgentSpec(
    name="code_reviewer",
    system_prompt=loader.get_system_prompt("code_reviewer"),
    next_step_prompt=loader.get_next_step_prompt("code_reviewer"),
    ...
)
```

---

## 高级：动态 max_steps

运行时覆盖 max_steps：

```python
result = await service.run(
    agent_name="code_reviewer",
    query="审查整个项目",
    agent_kwargs={"max_steps": 15},
)
```

---

## 检查清单

- [ ] spec.py 中 `name` 全局唯一
- [ ] `setup` 函数注册了需要的工具
- [ ] `agents/__init__.py` 中已注册
- [ ] 运行 CLI 测试通过
