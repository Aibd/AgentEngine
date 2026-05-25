import type {
  ArtifactBlock,
  ArtifactTrace,
  RunTrace,
  SseEvent,
  StepTrace,
  TodoItem,
  TodoStatus,
  ToolTrace,
  UsageSummary,
  UserQuestion,
} from "./types";

export function createEmptyTrace(): RunTrace {
  return {
    requestId: "",
    conversationId: "",
    query: "",
    status: "idle",
    steps: [],
    todos: [],
    pendingQuestions: [],
    artifacts: [],
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
        todos: [],
        pendingQuestions: [],
        artifacts: [],
        activeArtifactId: undefined,
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
    case "todos_updated":
      return {
        ...trace,
        todos: parseTodos(data.todos),
      };
    case "user_question_asked":
      return {
        ...trace,
        pendingQuestions: [...trace.pendingQuestions, parseQuestion(data)],
      };
    case "artifact_start": {
      const artifact = createArtifact(data);
      return {
        ...trace,
        status: "running",
        artifacts: upsertArtifact(trace.artifacts, artifact),
        activeArtifactId: artifact.id,
      };
    }
    case "artifact_section_started":
      return updateArtifact(trace, data, (artifact) => ({
        ...artifact,
        currentSection: stringValue(data.name),
      }));
    case "artifact_block_added":
      return updateArtifact(trace, data, (artifact) => ({
        ...artifact,
        blocks: [...artifact.blocks, data as unknown as ArtifactBlock],
      }));
    case "artifact_chart_ready":
      return updateArtifact(trace, data, (artifact) => {
        const chartId = stringValue(data.chart_id);
        if (!chartId) {
          return artifact;
        }
        return {
          ...artifact,
          charts: {
            ...artifact.charts,
            [chartId]: {
              url: stringValue(data.preview_url),
              title: stringValue(data.title),
            },
          },
        };
      });
    case "artifact_ready":
      return updateArtifact(trace, data, (artifact) => ({
        ...artifact,
        status: "ready",
      }), "completed");
    case "artifact_export_ready":
      return updateArtifact(trace, data, (artifact) => ({
        ...artifact,
        exports: stringRecord(data.exports),
      }));
    case "artifact_error":
      return updateArtifact(trace, data, (artifact) => ({
        ...artifact,
        status: "failed",
        error: stringValue(data.message) || "Artifact generation failed",
      }), "failed");
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

function parseTodos(value: unknown): TodoItem[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const out: TodoItem[] = [];
  for (const raw of value) {
    if (!raw || typeof raw !== "object") {
      continue;
    }
    const candidate = raw as Record<string, unknown>;
    const status = candidate.status;
    if (status !== "pending" && status !== "in_progress" && status !== "completed") {
      continue;
    }
    out.push({
      content: stringValue(candidate.content),
      activeForm: stringValue(candidate.activeForm),
      status: status as TodoStatus,
    });
  }
  return out;
}

function parseQuestion(data: Record<string, unknown>): UserQuestion {
  const optionsRaw = Array.isArray(data.options) ? data.options : [];
  const options: string[] = [];
  for (const opt of optionsRaw) {
    if (typeof opt === "string") {
      options.push(opt);
    }
  }
  return {
    questionId: stringValue(data.question_id),
    question: stringValue(data.question),
    options,
    multiple: Boolean(data.multiple),
  };
}

function createArtifact(data: Record<string, unknown>): ArtifactTrace {
  const id = stringValue(data.artifact_id) || stringValue(data.id) || crypto.randomUUID();
  const reportId = stringValue(data.report_id) || id;
  const rawType = stringValue(data.type);
  const type =
    rawType === "html" || rawType === "dashboard" || rawType === "financial_report"
      ? rawType
      : "financial_report";
  return {
    id,
    reportId,
    type,
    title: stringValue(data.title) || "Financial report",
    status: "streaming",
    blocks: [],
    charts: {},
  };
}

function updateArtifact(
  trace: RunTrace,
  data: Record<string, unknown>,
  updater: (artifact: ArtifactTrace) => ArtifactTrace,
  status?: RunTrace["status"],
): RunTrace {
  const id = stringValue(data.artifact_id) || stringValue(data.report_id) || trace.activeArtifactId;
  if (!id) {
    return trace;
  }
  let found = false;
  const artifacts = trace.artifacts.map((artifact) => {
    if (artifact.id !== id && artifact.reportId !== id) {
      return artifact;
    }
    found = true;
    return updater(artifact);
  });
  return found ? { ...trace, artifacts, activeArtifactId: id, status: status ?? trace.status } : trace;
}

function upsertArtifact(artifacts: ArtifactTrace[], next: ArtifactTrace): ArtifactTrace[] {
  const index = artifacts.findIndex((artifact) => artifact.id === next.id);
  if (index === -1) {
    return [...artifacts, next];
  }
  return artifacts.map((artifact, i) => (i === index ? { ...artifact, ...next } : artifact));
}

function stringRecord(value: unknown): Record<string, string> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  const out: Record<string, string> = {};
  for (const [key, raw] of Object.entries(value)) {
    if (typeof raw === "string") {
      out[key] = raw;
    }
  }
  return out;
}
