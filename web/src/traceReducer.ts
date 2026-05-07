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
  switch (event.responseType) {
    case "start":
      return {
        ...trace,
        requestId: event.reqId,
        conversationId: event.conversation_id,
        query: getQuery(event),
        status: "running",
        steps: [],
        usage: undefined,
        finalText: undefined,
        error: undefined,
      };
    case "step":
      return {
        ...trace,
        steps: [...trace.steps, createStep(event)],
      };
    case "thinking":
      return updateCurrentStep(trace, (step) => ({
        ...step,
        thinking: [...step.thinking, String(event.response ?? "")],
      }));
    case "text":
      return updateCurrentStep(trace, (step) => ({
        ...step,
        text: [...step.text, String(event.response ?? "")],
      }));
    case "tool_call_start":
      return updateCurrentStep(trace, (step) => ({
        ...step,
        tools: upsertTool(step.tools, createTool(event)),
      }));
    case "tool_result":
      return updateCurrentStep(trace, (step) => ({
        ...step,
        tools: completeTool(step.tools, event),
      }));
    case "step_end":
      return updateCurrentStep(trace, (step) => ({
        ...step,
        status: "completed",
        hasToolCalls: Boolean(event.resultMap?.has_tool_calls),
        elapsedSeconds: toNumber(event.resultMap?.elapsed_seconds),
      }));
    case "usage":
      return {
        ...trace,
        usage: createUsage(event),
      };
    case "result":
    case "final_result":
      return {
        ...trace,
        status: "completed",
        finalText: getFinalText(event),
      };
    case "error": {
      const payload = (typeof event.response === "object" && event.response !== null
        ? (event.response as Record<string, unknown>)
        : event.resultMap) as RunTrace["errorPayload"];
      return {
        ...trace,
        status: "failed",
        error: event.errorMsg ?? (payload?.message ?? "Unknown error"),
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

function createStep(event: SseEvent): StepTrace {
  return {
    turn: toNumber(event.resultMap?.turn) || 1,
    status: "running",
    thinking: [],
    text: [],
    tools: [],
  };
}

function createTool(event: SseEvent): ToolTrace {
  const resultMap = event.resultMap ?? {};
  return {
    id: String(resultMap.tool_call_id ?? crypto.randomUUID()),
    name: String(resultMap.tool ?? event.response ?? "tool"),
    arguments: asRecord(resultMap.arguments),
    status: "running",
  };
}

function completeTool(tools: ToolTrace[], event: SseEvent): ToolTrace[] {
  const resultMap = event.resultMap ?? {};
  const id = String(resultMap.tool_call_id ?? "");
  const ok = resultMap.ok !== false;
  const updated: ToolTrace = {
    id,
    name: String(resultMap.tool ?? "tool"),
    arguments: {},
    status: ok ? "completed" : "failed",
    result: resultMap.toolResult ?? event.response,
    elapsedSeconds: toNumber(resultMap.elapsed_seconds),
    errorType: typeof resultMap.error_type === "string" ? resultMap.error_type : undefined,
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

function createUsage(event: SseEvent): UsageSummary {
  return {
    promptTokens: toNumber(event.resultMap?.prompt_tokens),
    completionTokens: toNumber(event.resultMap?.completion_tokens),
    totalTokens: toNumber(event.resultMap?.total_tokens),
    totalSeconds: toNumber(event.resultMap?.total_seconds),
  };
}

function getQuery(event: SseEvent): string {
  if (event.resultMap && typeof event.resultMap.query === "string") {
    return event.resultMap.query;
  }
  if (typeof event.response === "string") {
    return event.response.replace(/^.*?:\s*/, "");
  }
  return "";
}

function getFinalText(event: SseEvent): string {
  if (event.resultMap && typeof event.resultMap.result === "string") {
    return event.resultMap.result;
  }
  if (event.resultMap && typeof event.resultMap.taskSummary === "string") {
    return event.resultMap.taskSummary;
  }
  return String(event.response ?? "");
}

function toNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
