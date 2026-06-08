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
  | "artifact_html_delta"
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

export type ArtifactTrace = {
  id: string;
  reportId: string;
  type: "financial_report" | "html" | "dashboard";
  title: string;
  status: "streaming" | "ready" | "failed";
  currentSection?: string;
  html: string;
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

export type SkillSource = "builtin" | "imported";

export type SkillSummary = {
  name: string;
  description: string;
  enabled: boolean;
  source: SkillSource;
  origin?: string;
};

export type SkillDetail = SkillSummary & {
  body: string;
};

export type MarketSkill = {
  id: string;
  name: string;
  description: string;
  category: string;
  icon: string;
  downloads: number;
  rating: number;
  source: string;
  installed: boolean;
};

export type Expert = {
  name: string;
  role: string;
  description: string;
  author: string;
  category: string;
  scenario: string;
  skills: string[];
  badge: string;
};

export type ScenarioGroup = {
  scenario: string;
  experts: Expert[];
};

export type ExpertTeam = {
  id: string;
  name: string;
  description: string;
  category: string;
  members: string[];
  member_experts: Expert[];
};

export type McpConnector = {
  id: string;
  name: string;
  transport: "stdio" | "sse";
  command: string;
  args: string[];
  env: Record<string, string>;
  url: string;
  enabled: boolean;
};
