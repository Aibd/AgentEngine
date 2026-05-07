import type { RunTrace, SseEvent, StepTrace, ToolTrace, UsageSummary } from "./types";

export function createEmptyTrace(): RunTrace {
  return {
    requestId: "",
    conversationId: "",
    query: "",
    status: "idle",
    steps: [],
  };
}

export function reduceTraceEvent(trace: RunTrace, event: SseEvent): RunTrace {
  const data = event.data;

  switch (event.event) {
    case "start":
      return {
        ...trace,
        requestId: stringValue(data.request_id),
        conversationId: stringValue(data.conversation_id),
        query: stringValue(data.query),
        status: "running",
        steps: [],
        usage: undefined,
        finalText: undefined,
        error: undefined,
        errorPayload: undefined,
      };
    case "step":
      return {
        ...trace,
        steps: [...trace.steps, createStep(data)],
      };
    case "thinking":
      return updateCurrentStep(trace, (step) => ({
        ...step,
        thinking: [...step.thinking, stringValue(data.delta)],
      }));
    case "text":
      return updateCurrentStep(trace, (step) => ({
        ...step,
        text: [...step.text, stringValue(data.delta)],
      }));
    case "tool_call_start":
      return updateCurrentStep(trace, (step) => ({
        ...step,
        tools: upsertTool(step.tools, createTool(data)),
      }));
    case "tool_result":
      return updateCurrentStep(trace, (step) => ({
        ...step,
        tools: completeTool(step.tools, data),
      }));
    case "step_end":
      return updateCurrentStep(trace, (step) => ({
        ...step,
        status: "completed",
        hasToolCalls: Boolean(data.has_tool_calls),
        elapsedSeconds: msToSeconds(data.elapsed_ms),
      }));
    case "usage":
      return {
        ...trace,
        usage: createUsage(data),
      };
    case "done":
    case "final_result":
      return {
        ...trace,
        status: "completed",
        finalText: stringValue(data.result ?? data.value),
      };
    case "error": {
      const payload = data as RunTrace["errorPayload"];
      return {
        ...trace,
        status: "failed",
        error: payload?.message ?? "Unknown error",
        errorPayload: payload,
      };
    }
    default:
      return trace;
  }
}

export function reduceTraceEvents(events: SseEvent[]): RunTrace {
  return events.reduce(reduceTraceEvent, createEmptyTrace());
}

function createStep(data: Record<string, unknown>): StepTrace {
  return {
    turn: toNumber(data.turn) || 1,
    status: "running",
    thinking: [],
    text: [],
    tools: [],
  };
}

function createTool(data: Record<string, unknown>): ToolTrace {
  return {
    id: stringValue(data.tool_call_id) || crypto.randomUUID(),
    name: stringValue(data.tool) || "tool",
    arguments: asRecord(data.arguments),
    status: "running",
  };
}

function completeTool(tools: ToolTrace[], data: Record<string, unknown>): ToolTrace[] {
  const id = stringValue(data.tool_call_id);
  const ok = data.ok !== false;
  const updated: ToolTrace = {
    id,
    name: stringValue(data.tool) || "tool",
    arguments: {},
    status: ok ? "completed" : "failed",
    result: data.result,
    elapsedSeconds: msToSeconds(data.elapsed_ms),
    errorType: typeof data.error_type === "string" ? data.error_type : undefined,
  };

  if (!id) {
    return [...tools, updated];
  }

  let found = false;
  const next = tools.map((tool) => {
    if (tool.id !== id) {
      return tool;
    }
    found = true;
    return {
      ...tool,
      ...updated,
      arguments: tool.arguments,
    };
  });
  return found ? next : [...next, updated];
}

function upsertTool(tools: ToolTrace[], nextTool: ToolTrace): ToolTrace[] {
  const index = tools.findIndex((tool) => tool.id === nextTool.id);
  if (index === -1) {
    return [...tools, nextTool];
  }
  return tools.map((tool, i) => (i === index ? { ...tool, ...nextTool } : tool));
}

function updateCurrentStep(trace: RunTrace, updater: (step: StepTrace) => StepTrace): RunTrace {
  const current = trace.steps.at(-1);
  if (!current) {
    return trace;
  }
  return {
    ...trace,
    steps: trace.steps.map((step, index) =>
      index === trace.steps.length - 1 ? updater(step) : step,
    ),
  };
}

function createUsage(data: Record<string, unknown>): UsageSummary {
  return {
    promptTokens: toNumber(data.prompt_tokens),
    completionTokens: toNumber(data.completion_tokens),
    totalTokens: toNumber(data.total_tokens),
    totalSeconds: toNumber(data.total_seconds),
  };
}

function toNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function msToSeconds(value: unknown): number {
  return toNumber(value) / 1000;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
