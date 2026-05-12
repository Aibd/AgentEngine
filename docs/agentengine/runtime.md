# Runtime

`src/agentengine/runtime/` contains the single think/act loop, lifecycle
runner, runtime events, run state, cancellation, and compaction support.

## Loop

`run_turn(agent, context, query, ...)` owns:

1. setup and memory hydration
2. `UserPromptSubmit` hook
3. an unbounded `while True` think/act loop
4. optional auto-compaction before each model call
5. LLM streaming and tool-call accumulation
6. tool execution through approval, hooks, quota, and `ExecPolicy`
7. `AfterTurn` hook evaluation
8. usage reporting, persistence, and teardown

There is no engine-level step cap. The loop stops when the model returns no
`tool_calls`, an `AfterTurn` hook returns `HookResult.stop()`, compaction fails,
the cancellation token is triggered, or a terminal provider/runtime error is
raised.

## TurnRunner

`TurnRunner` wraps `run_turn()` with run ids, event fanout, JSONL logs, file
access tracking, `SessionStart`, terminal `Stop`, and `RunState` classification.

## Safety

- `CancellationToken` is stored in `context.extras["cancellation_token"]`.
- `Compactor` / `LLMSummaryCompactor` compress history when
  `RunConfig.auto_compact_tokens` is exceeded.
- `AfterTurnPayload` lets host code stop weak or domain-specific agents without
  reintroducing a hard-coded step limit.
