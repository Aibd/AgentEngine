# Create An Agent

Agents are declared with `AgentPreset` and registered in an `AgentEngine`.

```python
from agentengine import AgentPreset
from agentengine.base.context import AgentContext
from agentengine.tools.builtin.read_file_tool import ReadFileTool


async def setup(context: AgentContext) -> None:
    if context.tool_collection is not None:
        context.tool_collection.add(ReadFileTool())


CODE_REVIEWER = AgentPreset(
    name="code_reviewer",
    description="Python code review agent",
    instructions=(
        "You are a Python code reviewer. Find correctness bugs, missing tests, "
        "and risky behavior. Keep findings concise and evidence-based."
    ),
    auto_compact_tokens=120_000,
    setup=setup,
)
```

Register it:

```python
from agentengine import AgentEngine

engine = AgentEngine(presets={"code_reviewer": CODE_REVIEWER})
```

Run it:

```python
result = await engine.run(
    agent_name="code_reviewer",
    query="Review this change",
    context=context,
)
```

`max_steps` / `max_turns` are not supported. Use an `AfterTurn` hook for
business-specific stop conditions, `auto_compact_tokens` for long histories,
`ExecPolicy` for tool governance, and `AgentEngine.interrupt(request_id)` for
external cancellation.
