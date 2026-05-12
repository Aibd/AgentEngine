# AgentEngine API

See also [INTEGRATION.md](../INTEGRATION.md) and [PUBLIC_API.md](PUBLIC_API.md).

## AgentEngine

```python
from agentengine import AgentContext, AgentEngine, AgentPreset

engine = AgentEngine(
    presets={"chat": AgentPreset(name="chat", instructions="Answer briefly.")}
)

context = AgentContext(request_id="req-1", query="hello", llm=llm)
result = await engine.run(agent_name="chat", query="hello", context=context)
```

Use either `presets` or `config_resolver`, and provide an LLM through
`context.llm` or `llm_factory`.

## AgentPreset / RunConfig

`AgentPreset` is the SDK-friendly declaration. It compiles into `RunConfig`:

```python
AgentPreset(
    name="support",
    instructions="You are a concise support assistant.",
    auto_compact_tokens=120_000,
)
```

`RunConfig` contains runtime setup, teardown, memory limits, auto-compaction,
and extras.

`max_turns` / `max_steps` have been removed. The engine loop stops only when:

- the model returns no `tool_calls`
- an `AfterTurn` hook returns `HookResult.stop()`
- auto-compaction fails with `ContextWindowExceededError`
- `AgentEngine.interrupt(request_id)` cancels the run
- the provider/API raises a terminal error

## Safety Controls

- `AgentPreset` appends the default self-discipline system instruction.
- `auto_compact_tokens` enables automatic history compaction.
- `context.extras["hooks"] = HookManager()` enables `PreToolUse`, `AfterTurn`,
  and terminal `Stop` hooks.
- `context.extras["exec_policy"] = ExecPolicy(...)` gates tools before
  execution.
- `AgentEngine.interrupt(request_id)` cancels an active run.

## Runtime Events

`RuntimeEvent` objects can be sent to SSE, WebSocket, or any custom sink via
`on_event`. Common events include `RunStarted`, `TurnStarted`, `TextDelta`,
`ToolCallStarted`, `ToolCallCompleted`, `UsageReport`, `RunCompleted`,
`RunFailed`, and `RunCancelled`.
