import type { CapabilitySummary, ReportFileSummary, ResponseType, SseEvent } from "./types";

type TraceHandler = (event: SseEvent) => void;
type DoneHandler = () => void;

const EVENT_TYPES: ResponseType[] = [
  "start",
  "step",
  "thinking",
  "text",
  "tool_call_start",
  "tool_result",
  "step_end",
  "usage",
  "done",
  "error",
  "task",
  "tool_thought",
  "search_result",
  "final_result",
  "todos_updated",
  "user_question_asked",
  "artifact_start",
  "artifact_section_started",
  "artifact_html_delta",
  "artifact_chart_ready",
  "artifact_ready",
  "artifact_export_ready",
  "artifact_error",
];

const REPORT_STREAM_MAX_RECONNECTS = 20;

export function runAgentTrace(
  query: string,
  onEvent: TraceHandler,
  onDone: DoneHandler,
  agentName = "deep_research",
  conversationId = "web-conversation",
): () => void {
  const controller = new AbortController();
  const params = new URLSearchParams({
    query,
    agent_name: agentName,
    conversation_id: conversationId,
  });

  void consumeSse(`/api/runs/stream?${params.toString()}`, controller.signal, onEvent)
    .catch((error: unknown) => {
      if (controller.signal.aborted) {
        return;
      }
      onEvent(createErrorEvent(error));
    })
    .finally(onDone);

  return () => controller.abort();
}

export function runReportTrace(
  title: string,
  intent: string,
  fileIds: string[],
  skill: string,
  onEvent: TraceHandler,
  onDone: DoneHandler,
  conversationId = "web-conversation",
): () => void {
  const controller = new AbortController();

  void createAndStreamReport(title, intent, fileIds, skill, conversationId, controller.signal, onEvent)
    .catch((error: unknown) => {
      if (controller.signal.aborted) {
        return;
      }
      onEvent(createErrorEvent(error));
    })
    .finally(onDone);

  return () => controller.abort();
}

export async function uploadReportFile(
  file: File,
  conversationId = "web-conversation",
): Promise<ReportFileSummary> {
  const params = new URLSearchParams({
    filename: file.name,
    conversation_id: conversationId,
  });
  const response = await fetch(`/api/report-files?${params.toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  });
  if (!response.ok) {
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = typeof body.detail === "string" ? `: ${body.detail}` : "";
    } catch {
      detail = "";
    }
    throw new Error(`upload failed: ${response.status}${detail}`);
  }
  return (await response.json()) as ReportFileSummary;
}

export async function fetchCapabilities(): Promise<CapabilitySummary> {
  const response = await fetch("/api/capabilities");
  if (!response.ok) {
    throw new Error(`capabilities request failed: ${response.status}`);
  }
  return (await response.json()) as CapabilitySummary;
}

export function connectEventSource(url: string, onEvent: TraceHandler): () => void {
  const source = new EventSource(url);
  for (const eventType of EVENT_TYPES) {
    source.addEventListener(eventType, (message) => {
      onEvent({
        event: eventType,
        data: JSON.parse((message as MessageEvent).data) as Record<string, unknown>,
      });
    });
  }
  return () => source.close();
}

async function consumeSse(
  url: string,
  signal: AbortSignal,
  onEvent: TraceHandler,
): Promise<boolean> {
  const response = await fetch(url, {
    headers: { Accept: "text/event-stream" },
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`stream request failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawTerminalEvent = false;

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const parsed = parseSsePart(part);
      if (parsed) {
        onEvent(parsed);
        if (isTerminalEvent(parsed)) {
          sawTerminalEvent = true;
        }
      }
    }
  }
  return sawTerminalEvent;
}

async function createAndStreamReport(
  title: string,
  intent: string,
  fileIds: string[],
  skill: string,
  conversationId: string,
  signal: AbortSignal,
  onEvent: TraceHandler,
): Promise<void> {
  const response = await fetch("/api/reports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title,
      intent,
      skill,
      file_ids: fileIds,
      conversation_id: conversationId,
    }),
    signal,
  });
  if (!response.ok) {
    throw new Error(`create report failed: ${response.status}`);
  }
  const report = (await response.json()) as { id?: string };
  if (!report.id) {
    throw new Error("create report response did not include id");
  }
  await consumeReportSseWithReconnect(report.id, signal, onEvent);
}

async function consumeReportSseWithReconnect(
  reportId: string,
  signal: AbortSignal,
  onEvent: TraceHandler,
): Promise<void> {
  const streamUrl = `/api/reports/${encodeURIComponent(reportId)}/stream`;
  let attempts = 0;

  while (!signal.aborted) {
    try {
      const sawTerminalEvent = await consumeSse(streamUrl, signal, onEvent);
      if (sawTerminalEvent || signal.aborted) {
        return;
      }
      throw new Error("report stream ended before completion");
    } catch (error) {
      if (signal.aborted) {
        return;
      }
      attempts += 1;
      if (attempts > REPORT_STREAM_MAX_RECONNECTS) {
        throw error;
      }
      await waitForReconnect(attempts, signal);
    }
  }
}

async function waitForReconnect(attempt: number, signal: AbortSignal): Promise<void> {
  if (typeof document !== "undefined" && document.visibilityState === "hidden") {
    await waitUntilVisible(signal);
  }
  const delayMs = Math.min(5000, 400 * attempt);
  await sleep(delayMs, signal);
}

function waitUntilVisible(signal: AbortSignal): Promise<void> {
  if (typeof document === "undefined" || document.visibilityState !== "hidden") {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      signal.removeEventListener("abort", handleAbort);
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState !== "hidden") {
        cleanup();
        resolve();
      }
    };
    const handleAbort = () => {
      cleanup();
      reject(new DOMException("Aborted", "AbortError"));
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    signal.addEventListener("abort", handleAbort, { once: true });
  });
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      signal.removeEventListener("abort", handleAbort);
      resolve();
    }, ms);
    const handleAbort = () => {
      window.clearTimeout(timeout);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal.addEventListener("abort", handleAbort, { once: true });
  });
}

function parseSsePart(part: string): SseEvent | null {
  let event = "message";
  const dataLines: string[] = [];

  for (const rawLine of part.split("\n")) {
    const line = rawLine.trimEnd();
    if (!line || line.startsWith(":")) {
      continue;
    }
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  if (!dataLines.length || !isResponseType(event)) {
    return null;
  }

  return {
    event,
    data: JSON.parse(dataLines.join("\n")) as Record<string, unknown>,
  };
}

function createErrorEvent(error: unknown): SseEvent {
  const message = error instanceof Error ? error.message : String(error);
  return {
    event: "error",
    data: {
      code: "web_stream_error",
      message,
      category: "runtime",
      retryable: false,
      request_id: "web-client",
      conversation_id: "web-conversation",
    },
  };
}

function isResponseType(value: string): value is ResponseType {
  return EVENT_TYPES.includes(value as ResponseType);
}

function isTerminalEvent(event: SseEvent): boolean {
  return event.event === "done"
    || event.event === "final_result"
    || event.event === "error"
    || event.event === "artifact_ready"
    || event.event === "artifact_error";
}
