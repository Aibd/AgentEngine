import type { CapabilitySummary, ResponseType, SseEvent } from "./types";

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
];

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
): Promise<void> {
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
      }
    }
  }
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
