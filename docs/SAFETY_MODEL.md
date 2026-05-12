# Safety Model

AgentEngine no longer uses `max_turns` or `max_steps` as a loop safety
mechanism. The engine runs the loop; stop decisions come from explicit runtime
signals.

## Six Layers

1. **Model self-discipline**  
   `AgentPreset` appends a default system instruction telling the model to keep
   working until the user request is resolved and stop only when confident.

2. **Auto-compaction**  
   Set `auto_compact_tokens` on `RunConfig` / `AgentPreset`. When memory exceeds
   the threshold, the runtime uses a configured `Compactor`, or
   `LLMSummaryCompactor` with the current LLM, to summarize older history. If
   compaction fails, the run fails closed with `ContextWindowExceededError`.

3. **Terminal API errors**  
   Context-window and usage-limit failures are structured as
   `ContextWindowExceededError` and `UsageLimitReachedError` so hosts can report
   them clearly.

4. **Hooks**  
   `PreToolUse` can block tool calls. `AfterTurn` can return
   `HookResult.stop()` to end a loop normally. Terminal `Stop` remains a cleanup
   hook.

5. **ExecPolicy + approval**  
   `ExecPolicy` runs before tools and supports prefix-based `allow`, `deny`,
   and `ask`. `ask` emits `ApprovalRequired`; destructive-tool approval remains
   available through the existing approval gate.

6. **External interrupt**  
   `AgentEngine.interrupt(request_id)` cancels an active run through the runtime
   cancellation token and task cancellation.

## Guidance

For small or weak models, configure an `AfterTurn` stop hook that checks your
domain-specific progress signal. For production tools, combine `ExecPolicy`
with approval gates for state-changing commands. For long-running agents, set a
realistic `auto_compact_tokens` threshold and consider injecting a cheaper
summary model through the `Compactor` interface.
