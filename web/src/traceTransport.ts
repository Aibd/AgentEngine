import type { SseEvent } from "./types";

type TraceHandler = (event: SseEvent) => void;
type DoneHandler = () => void;

export function runAgentTrace(
  query: string,
  onEvent: TraceHandler,
  onDone: DoneHandler,
  agentName = "deep_research",
): () => void {
  const controller = new AbortController();
  const params = new URLSearchParams({
    query,
    agent_name: agentName,
    conversation_id: "web-conversation",
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

export function connectEventSource(url: string, onEvent: TraceHandler): () => void {
  const source = new EventSource(url);
  source.onmessage = (message) => {
    onEvent(JSON.parse(message.data) as SseEvent);
  };
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
      const data = part
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (data) {
        onEvent(JSON.parse(data) as SseEvent);
      }
    }
  }
}

function createErrorEvent(error: unknown): SseEvent {
  const message = error instanceof Error ? error.message : String(error);
  return {
    responseType: "error",
    response: { message },
    responseAll: "",
    useTimes: 0,
    reqId: "web-client",
    errorMsg: message,
    resultMap: null,
    conversation_id: "web-conversation",
    finished: true,
  };
}
