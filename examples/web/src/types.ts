export type ResponseType =
  | "start"
  | "step"
  | "thinking"
  | "text"
  | "tool_call_start"
  | "tool_result"
  | "step_end"
  | "usage"
  | "done"
  | "error"
  | "task"
  | "tool_thought"
  | "search_result"
  | "final_result"
  | "todos_updated"
  | "user_question_asked"
  | "artifact_start"
  | "artifact_section_started"
  | "artifact_block_added"
  | "artifact_chart_ready"
  | "artifact_ready"
  | "artifact_export_ready"
  | "artifact_error";

export type TodoStatus = "pending" | "in_progress" | "completed";

export type TodoItem = {
  content: string;
  activeForm: string;
  status: TodoStatus;
};

export type UserQuestion = {
  questionId: string;
  question: string;
  options: string[];
  multiple: boolean;
};

export type ArtifactBlockType =
  | "heading"
  | "paragraph"
  | "table"
  | "chart"
  | "kpi"
  | "callout"
  | "page_break";

export type ArtifactKpi = {
  label: string;
  value: string;
  delta?: string;
  trend?: "up" | "down" | "flat";
};

export type ArtifactChart = {
  kind?: string;
  title?: string;
  x?: string[];
  series?: { name: string; data: Array<number | null> }[];
  y_format?: "currency" | "percent" | "number";
};

export type ArtifactBlock = {
  type: ArtifactBlockType;
  level?: number;
  text?: string;
  headers?: string[];
  rows?: Array<Array<string | number | null>>;
  chart_id?: string;
  chart?: ArtifactChart;
  kpis?: ArtifactKpi[];
  style?: Record<string, unknown>;
};

export type ArtifactTrace = {
  id: string;
  reportId: string;
  type: "financial_report" | "html" | "dashboard";
  title: string;
  status: "streaming" | "ready" | "failed";
  currentSection?: string;
  blocks: ArtifactBlock[];
  charts: Record<string, { url?: string; title?: string }>;
  exports?: Record<string, string>;
  error?: string;
};

export type ParsedSheetPreview = {
  name: string;
  headers: string[];
  rows: string[][];
  row_count: number;
  column_count: number;
};

export type ParsedReportFile = {
  file_id: string;
  filename: string;
  extension: string;
  size_bytes: number;
  sheets: ParsedSheetPreview[];
  text_preview: string;
  page_count: number;
  warnings: string[];
};

export type ReportFileSummary = {
  id: string;
  filename: string;
  size_bytes: number;
  parsed: ParsedReportFile;
};

export type SseEvent = {
  event: ResponseType;
  data: Record<string, unknown>;
};

export type UsageSummary = {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  totalSeconds: number;
};

export type ToolTrace = {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  status: "running" | "completed" | "failed";
  result?: unknown;
  elapsedSeconds?: number;
  errorType?: string;
};

export type StepTrace = {
  turn: number;
  status: "running" | "completed";
  thinking: string[];
  text: string[];
  tools: ToolTrace[];
  hasToolCalls?: boolean;
  elapsedSeconds?: number;
};

export type ErrorPayload = {
  code?: string;
  message?: string;
  category?: string;
  retryable?: boolean;
  status_code?: number;
  details?: Record<string, unknown>;
};

export type RunTrace = {
  requestId: string;
  conversationId: string;
  query: string;
  status: "idle" | "running" | "completed" | "failed";
  steps: StepTrace[];
  usage?: UsageSummary;
  finalText?: string;
  error?: string;
  errorPayload?: ErrorPayload;
  todos: TodoItem[];
  pendingQuestions: UserQuestion[];
  artifacts: ArtifactTrace[];
  activeArtifactId?: string;
};

export type CapabilityItem = {
  name: string;
  description: string;
};

export type CapabilitySummary = {
  agents: CapabilityItem[];
  tools: CapabilityItem[];
  skills: CapabilityItem[];
};
