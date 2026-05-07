import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Clock3,
  Menu,
  Loader2,
  PanelLeftClose,
  Plus,
  Send,
  Sparkles,
  Wrench,
} from "lucide-react";
import { createEmptyTrace, reduceTraceEvent } from "./traceReducer";
import { runAgentTrace } from "./traceTransport";
import { translateError } from "./friendlyErrors";
import type { ErrorPayload, RunTrace, StepTrace, ToolTrace, UsageSummary } from "./types";

const samplePrompts = [
  "读取 README.md 和 docs/API.md，解释现在的事件协议",
  "用 read_file 工具检查项目结构并总结",
  "检查 Web 端展示需要哪些字段",
];

type ChatSession = {
  id: string;
  title: string;
  query: string;
  submittedQuery: string;
  trace: RunTrace;
};

export function App() {
  const [isRunning, setIsRunning] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sessions, setSessions] = useState<ChatSession[]>([
    createChatSession("welcome", "当前会话", samplePrompts[0]),
  ]);
  const [activeSessionId, setActiveSessionId] = useState("welcome");
  const stopRef = useRef<null | (() => void)>(null);
  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) ?? sessions[0],
    [activeSessionId, sessions],
  );
  const trace = activeSession.trace;
  const query = activeSession.query;
  const submittedQuery = activeSession.submittedQuery;

  useEffect(() => {
    return () => stopRef.current?.();
  }, []);

  function startRun(nextQuery = query) {
    const cleaned = nextQuery.trim();
    if (!cleaned) {
      return;
    }
    const runSessionId = activeSessionId;
    stopRef.current?.();
    setSessions((current) =>
      updateSession(current, runSessionId, (session) => ({
        ...session,
        title: titleFromQuery(cleaned),
        query: cleaned,
        submittedQuery: cleaned,
        trace: createEmptyTrace(),
      })),
    );
    setIsRunning(true);
    stopRef.current = runAgentTrace(
      cleaned,
      (event) => {
        setSessions((current) =>
          updateSession(current, runSessionId, (session) => ({
            ...session,
            trace: reduceTraceEvent(session.trace, event),
          })),
        );
        if (event.event === "done" || event.event === "error") {
          setIsRunning(false);
        }
      },
      () => setIsRunning(false),
    );
  }

  function stopRun() {
    stopRef.current?.();
    stopRef.current = null;
    setIsRunning(false);
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isRunning) {
      stopRun();
      return;
    }
    startRun();
  }

  function newSession() {
    stopRun();
    const id = crypto.randomUUID();
    setSessions((current) => [createChatSession(id, "新会话"), ...current]);
    setActiveSessionId(id);
  }

  function selectSession(id: string) {
    stopRun();
    setActiveSessionId(id);
  }

  function updateActiveQuery(value: string) {
    setSessions((current) =>
      updateSession(current, activeSessionId, (session) => ({
        ...session,
        query: value,
      })),
    );
  }

  return (
    <main className={sidebarOpen ? "app-layout" : "app-layout sidebar-collapsed"}>
      <aside className="sidebar" aria-label="会话历史">
        <div className="sidebar-top">
          <button className="sidebar-icon" type="button" onClick={() => setSidebarOpen(false)} title="折叠侧边栏">
            <PanelLeftClose size={18} />
          </button>
          <button className="new-chat-button" type="button" onClick={newSession}>
            <Plus size={17} />
            <span>新建会话</span>
          </button>
        </div>
        <div className="history-list">
          {sessions.map((session) => (
            <button
              key={session.id}
              className={session.id === activeSessionId ? "history-item is-active" : "history-item"}
              type="button"
              onClick={() => selectSession(session.id)}
            >
              {session.title}
            </button>
          ))}
        </div>
      </aside>

      <section className="chat-shell">
        {!sidebarOpen ? (
          <button className="floating-sidebar-toggle" type="button" onClick={() => setSidebarOpen(true)} title="展开侧边栏">
            <Menu size={19} />
          </button>
        ) : null}

        <section className="message-lane" aria-label="Chat">
          {trace.status === "idle" ? (
            <EmptyChat
              onPick={(prompt) => {
                updateActiveQuery(prompt);
                startRun(prompt);
              }}
            />
          ) : (
            <Conversation trace={trace} submittedQuery={submittedQuery} isRunning={isRunning} />
          )}
        </section>

        <form className="composer" onSubmit={onSubmit}>
          <input
            value={query}
            onChange={(event) => updateActiveQuery(event.target.value)}
            placeholder="输入一个请求"
            disabled={isRunning}
          />
          <button
            className={isRunning ? "composer-action is-running" : "composer-action"}
            type={isRunning ? "button" : "submit"}
            onClick={isRunning ? stopRun : undefined}
            disabled={!isRunning && !query.trim()}
            title={isRunning ? "Stop" : "Send"}
          >
            {isRunning ? <Loader2 className="spin" size={19} /> : <Send size={19} />}
          </button>
        </form>
      </section>
    </main>
  );
}

function Conversation({
  trace,
  submittedQuery,
  isRunning,
}: {
  trace: RunTrace;
  submittedQuery: string;
  isRunning: boolean;
}) {
  const hasStreamedText = trace.steps.some((step) => step.text.join("").trim().length > 0);

  return (
    <div className="conversation">
      <article className="message-row user-row">
        <div className="user-bubble">{submittedQuery || trace.query}</div>
      </article>

      <article className="message-row assistant-row">
        <div className="assistant-avatar">
          <Sparkles size={17} />
        </div>
        <div className="assistant-message">
          {trace.steps.map((step) => (
            <StepGroup key={step.turn} step={step} />
          ))}

          {isRunning ? (
            <div className="live-row">
              <Loader2 className="spin" size={16} />
              <span>正在执行</span>
            </div>
          ) : null}

          {trace.finalText && !hasStreamedText ? <FinalCard text={trace.finalText} usage={trace.usage} /> : null}
          {trace.status === "completed" && hasStreamedText && trace.usage ? <CompletionCard usage={trace.usage} /> : null}
          {trace.error || trace.errorPayload ? (
            <ErrorCard message={trace.error ?? ""} payload={trace.errorPayload} />
          ) : null}
        </div>
      </article>
    </div>
  );
}

function StepGroup({ step }: { step: StepTrace }) {
  const hasOnlyText = step.text.length > 0 && step.thinking.length === 0 && step.tools.length === 0;

  return (
    <section className={hasOnlyText ? "step-group plain-step" : "step-group"}>
      {!hasOnlyText ? (
        <div className="step-heading">
          <span className="step-title">Step {step.turn}</span>
          <span className="step-time">{formatSeconds(step.elapsedSeconds)}</span>
        </div>
      ) : null}

      {step.thinking.length ? <ThinkingBlock chunks={step.thinking} running={step.status === "running"} /> : null}

      <div className="tool-grid">
        {step.tools.map((tool) => (
          <ToolCard key={tool.id} tool={tool} />
        ))}
      </div>

      {step.text.length ? <AssistantText chunks={step.text} /> : null}
    </section>
  );
}

function ThinkingBlock({ chunks, running }: { chunks: string[]; running: boolean }) {
  const [open, setOpen] = useState(running);

  useEffect(() => {
    setOpen(running);
  }, [running]);

  return (
    <div className="thinking-block">
      <button type="button" onClick={() => setOpen((value) => !value)}>
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <span>Thinking</span>
      </button>
      {open ? <p>{chunks.join("")}</p> : null}
    </div>
  );
}

function AssistantText({ chunks }: { chunks: string[] }) {
  return <MarkdownBlock className="assistant-text" content={chunks.join("")} />;
}

function ToolCard({ tool }: { tool: ToolTrace }) {
  const [open, setOpen] = useState(false);
  const failed = tool.status === "failed";
  const running = tool.status === "running";

  return (
    <article className={`tool-card ${failed ? "is-failed" : ""}`}>
      <button className="tool-card-head" type="button" onClick={() => setOpen((value) => !value)}>
        <span className="tool-name">
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          <Wrench size={16} />
          <strong>{tool.name}</strong>
        </span>
        {running || failed ? (
          <span className={`status-pill ${tool.status}`}>
            {running ? <Loader2 className="spin" size={12} /> : <CircleAlert size={12} />}
            {tool.status}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="tool-card-content">
          <CodeBlock label="Arguments" value={tool.arguments} />
          {tool.result !== undefined ? <CodeBlock label="Result" value={tool.result} /> : null}
          <div className="tool-meta">
            <Clock3 size={14} />
            <span>{formatSeconds(tool.elapsedSeconds)}</span>
            {tool.errorType ? <span>{tool.errorType}</span> : null}
          </div>
        </div>
      ) : null}
    </article>
  );
}

function CodeBlock({ label, value }: { label: string; value: unknown }) {
  const rendered = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <div className="code-block">
      <span>{label}</span>
      <pre>{rendered}</pre>
    </div>
  );
}

function FinalCard({ text, usage }: { text: string; usage?: UsageSummary }) {
  return (
    <article className="final-card">
      <MarkdownBlock content={text} />
      {usage ? <UsageLine usage={usage} /> : null}
    </article>
  );
}

function CompletionCard({ usage }: { usage: UsageSummary }) {
  return (
    <article className="completion-card">
      <UsageLine usage={usage} compact />
    </article>
  );
}

function UsageLine({ usage, compact = false }: { usage: UsageSummary; compact?: boolean }) {
  return (
    <span className={compact ? "usage-line compact" : "usage-line"}>
      {usage.totalTokens.toLocaleString()} tokens / {formatSeconds(usage.totalSeconds)}
    </span>
  );
}

function ErrorCard({ message, payload }: { message: string; payload?: ErrorPayload }) {
  const friendly = translateError(payload ?? { message });
  return (
    <article className="error-card">
      <CircleAlert size={19} />
      <div className="error-body">
        <strong>{friendly.headline}</strong>
        <p>{friendly.detail}</p>
        {friendly.hint ? <p className="error-hint">💡 {friendly.hint}</p> : null}
        {payload?.code ? (
          <p className="error-meta">
            code: {payload.code}
            {payload.category ? `   ·   category: ${payload.category}` : ""}
            {friendly.retryable ? "   ·   可重试" : ""}
          </p>
        ) : null}
      </div>
    </article>
  );
}

function EmptyChat({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="empty-chat">
      <div className="empty-mark">
        <Sparkles size={22} />
      </div>
      <h1>Agent Core</h1>
      <div className="prompt-grid">
        {samplePrompts.map((prompt) => (
          <button key={prompt} type="button" onClick={() => onPick(prompt)}>
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}

function MarkdownBlock({ content, className = "" }: { content: string; className?: string }) {
  return (
    <div className={`markdown ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

function formatSeconds(value?: number): string {
  if (!value) {
    return "0.00s";
  }
  return `${value.toFixed(2)}s`;
}

function createChatSession(id: string, title: string, query = ""): ChatSession {
  return {
    id,
    title,
    query,
    submittedQuery: "",
    trace: createEmptyTrace(),
  };
}

function updateSession(
  sessions: ChatSession[],
  id: string,
  updater: (session: ChatSession) => ChatSession,
): ChatSession[] {
  return sessions.map((session) => (
    session.id === id ? updater(session) : session
  ));
}

function titleFromQuery(query: string): string {
  const title = query.length > 28 ? `${query.slice(0, 28)}...` : query;
  return title || "新会话";
}
