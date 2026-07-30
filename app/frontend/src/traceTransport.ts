import type {
  CapabilitySummary,
  Expert,
  ExpertTeam,
  MarketSkill,
  ReportFileSummary,
  ResponseType,
  ScenarioGroup,
  SkillDetail,
  SkillSummary,
  SseEvent,
} from "./types";
import { apiFetch } from "./auth";

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
  agentName = "general_chat",
  conversationId = "web-conversation",
  skill = "",
): () => void {
  const controller = new AbortController();
  const params = new URLSearchParams({
    query,
    agent_name: agentName,
    conversation_id: conversationId,
  });
  if (skill && skill !== "chat") {
    params.set("skill", skill);
  }

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
  resumeFromReportId = "",
): () => void {
  const controller = new AbortController();

  void createAndStreamReport(
    title,
    intent,
    fileIds,
    skill,
    conversationId,
    controller.signal,
    onEvent,
    resumeFromReportId,
  )
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
  const response = await apiFetch(`/api/report-files?${params.toString()}`, {
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
  const response = await apiFetch("/api/capabilities");
  if (!response.ok) {
    throw new Error(`capabilities request failed: ${response.status}`);
  }
  return (await response.json()) as CapabilitySummary;
}

export async function fetchSkills(): Promise<SkillSummary[]> {
  const response = await apiFetch("/api/skills");
  if (!response.ok) {
    throw new Error(`skills request failed: ${response.status}`);
  }
  const body = (await response.json()) as { skills?: SkillSummary[] };
  return body.skills ?? [];
}

export async function fetchSkillDetail(name: string): Promise<SkillDetail> {
  const response = await apiFetch(`/api/skills/${encodeURIComponent(name)}`);
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, "skill detail request failed"));
  }
  return (await response.json()) as SkillDetail;
}

export async function importSkillZip(file: File): Promise<SkillSummary> {
  const params = new URLSearchParams({ filename: file.name });
  const response = await apiFetch(`/api/skills/import?${params.toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/zip" },
    body: file,
  });
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, "导入失败"));
  }
  return (await response.json()) as SkillSummary;
}

export async function setSkillEnabled(name: string, enabled: boolean): Promise<SkillSummary> {
  const response = await apiFetch(`/api/skills/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, "更新失败"));
  }
  return (await response.json()) as SkillSummary;
}

export async function deleteSkill(name: string): Promise<void> {
  const response = await apiFetch(`/api/skills/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, "删除失败"));
  }
}

export function skillExportUrl(name: string): string {
  return `/api/skills/${encodeURIComponent(name)}/export`;
}

export async function fetchMarketSkills(category?: string, q?: string): Promise<MarketSkill[]> {
  const params = new URLSearchParams();
  if (category && category !== "全部") {
    params.set("category", category);
  }
  if (q && q.trim()) {
    params.set("q", q.trim());
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const response = await apiFetch(`/api/skill-market${suffix}`);
  if (!response.ok) {
    throw new Error(`skill market request failed: ${response.status}`);
  }
  const body = (await response.json()) as { skills?: MarketSkill[] };
  return body.skills ?? [];
}

export async function fetchMarketCategories(): Promise<string[]> {
  const response = await apiFetch("/api/skill-market/categories");
  if (!response.ok) {
    throw new Error(`skill market categories request failed: ${response.status}`);
  }
  const body = (await response.json()) as { categories?: string[] };
  return body.categories ?? [];
}

export async function installMarketSkill(entryId: string): Promise<SkillSummary> {
  const response = await apiFetch(`/api/skill-market/${encodeURIComponent(entryId)}/install`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, "安装失败"));
  }
  return (await response.json()) as SkillSummary;
}

export async function fetchExperts(category?: string, scenario?: string, q?: string): Promise<Expert[]> {
  const params = new URLSearchParams();
  if (category && category !== "全部") {
    params.set("category", category);
  }
  if (scenario) {
    params.set("scenario", scenario);
  }
  if (q && q.trim()) {
    params.set("q", q.trim());
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const response = await apiFetch(`/api/experts${suffix}`);
  if (!response.ok) {
    throw new Error(`experts request failed: ${response.status}`);
  }
  const body = (await response.json()) as { experts?: Expert[] };
  return body.experts ?? [];
}

export async function fetchExpertCategories(): Promise<string[]> {
  const response = await apiFetch("/api/experts/categories");
  if (!response.ok) {
    throw new Error(`expert categories request failed: ${response.status}`);
  }
  const body = (await response.json()) as { categories?: string[] };
  return body.categories ?? [];
}

export async function fetchExpertScenarios(): Promise<ScenarioGroup[]> {
  const response = await apiFetch("/api/experts/scenarios");
  if (!response.ok) {
    throw new Error(`expert scenarios request failed: ${response.status}`);
  }
  const body = (await response.json()) as { scenarios?: ScenarioGroup[] };
  return body.scenarios ?? [];
}

export async function fetchExpertTeams(category?: string, q?: string): Promise<ExpertTeam[]> {
  const params = new URLSearchParams();
  if (category && category !== "全部") {
    params.set("category", category);
  }
  if (q && q.trim()) {
    params.set("q", q.trim());
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const response = await apiFetch(`/api/expert-teams${suffix}`);
  if (!response.ok) {
    throw new Error(`expert teams request failed: ${response.status}`);
  }
  const body = (await response.json()) as { teams?: ExpertTeam[] };
  return body.teams ?? [];
}

export async function fetchExpertTeamCategories(): Promise<string[]> {
  const response = await apiFetch("/api/expert-teams/categories");
  if (!response.ok) {
    throw new Error(`expert team categories request failed: ${response.status}`);
  }
  const body = (await response.json()) as { categories?: string[] };
  return body.categories ?? [];
}

async function readErrorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) {
      return body.detail;
    }
  } catch {
    // Non-JSON body; fall through to the generic message.
  }
  return `${fallback} (${response.status})`;
}

async function consumeSse(
  url: string,
  signal: AbortSignal,
  onEvent: TraceHandler,
): Promise<boolean> {
  const response = await apiFetch(url, {
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
  resumeFromReportId = "",
): Promise<void> {
  const body: Record<string, unknown> = {
    title,
    intent,
    skill,
    file_ids: fileIds,
    conversation_id: conversationId,
  };
  if (resumeFromReportId) {
    body.resume_from_report_id = resumeFromReportId;
  }
  const response = await apiFetch("/api/reports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
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
