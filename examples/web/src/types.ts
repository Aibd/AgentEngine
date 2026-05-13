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
  | "user_question_asked";

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
