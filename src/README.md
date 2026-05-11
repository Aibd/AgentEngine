# src/

`src/` 现在只承载核心 SDK 包：

```text
agentengine/
  engine.py       AgentEngine SDK facade
  preset.py       AgentPreset declaration
  base/           AgentRun, AgentContext, AgentState
  runtime/        TurnRunner, run_turn(), RuntimeEvent
  llm/            LLMClient protocol and OpenAI-compatible client
  memory/         Message and Memory
  tools/          Tool, ToolCollection, ToolExecutor, builtin tools
  stream/         SSE adapter primitives
  persistence/    PersistencePort and SQLite reference adapter
  concurrency/    ConversationLockManager implementations
  enterprise/     Optional middleware
```

Example agents, FastAPI endpoints, and demo services live under
`examples/reference_app/` so downstream packages do not mistake them for core
engine APIs.
