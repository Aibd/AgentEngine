# Changelog

## 0.2.0

Breaking changes:

- Removed `max_turns` and `max_steps` from `RunConfig`, `AgentPreset`, and
  `agent_kwargs`.
- The runtime loop is now unbounded and stops through natural model completion,
  `AfterTurn` hooks, auto-compaction failure, explicit interrupt, or terminal
  provider/runtime errors.

Added:

- `Compactor` and `LLMSummaryCompactor` for auto-compaction.
- `ContextWindowExceededError` and `UsageLimitReachedError`.
- `HookResult.stop()` and `AfterTurnPayload`.
- `ExecPolicy` with allow/deny/ask prefix rules.
- `AgentEngine.interrupt(request_id)` for external cancellation.

Migration:

- Replace step-limit tests with natural completion, `AfterTurn` stop hooks, or
  explicit cancellation tests.
- Replace `agent_kwargs={"max_steps": N}` with a host-defined `AfterTurn` hook
  when a business stop condition is required.
- Configure `auto_compact_tokens` for long conversations.
