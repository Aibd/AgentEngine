# Errors

All structured framework errors inherit `AgentEngineError` and provide
`to_dict()` for SSE/WebSocket rendering.

Important runtime errors:

- `ContextWindowExceededError`: context exceeded and compaction could not
  recover. `LLMContextWindowError` remains as a backward-compatible subclass.
- `UsageLimitReachedError`: provider reports account/project usage exhaustion.
- `AgentCancelledError`: cooperative cancellation token was triggered.
- `ToolExecutionError`: tool-layer failure.

`TurnRunner` maps these to terminal reasons such as `context_exceeded`,
`usage_limit_reached`, `cancelled`, `tool_failed`, and `runtime_failed`.
