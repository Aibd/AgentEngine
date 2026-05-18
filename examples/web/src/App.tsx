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
import { fetchCapabilities, runAgentTrace } from "./traceTransport";
import { translateError } from "./friendlyErrors";
import type {
  CapabilitySummary,
  ErrorPayload,
  RunTrace,
  StepTrace,
  TodoItem,
  ToolTrace,
  UsageSummary,
  UserQuestion,
} from "./types";

const samplePrompts = [
  "阅读 README.md 和 docs/API.md，给我一份项目概览",
  "用 read_file 看一下前端入口在哪里",
  "解释 Web 流式事件协议",
];

type ChatTurn = {
  id: string;
  submittedQuery: string;
  trace: RunTrace;
};

type ChatSession = {
  id: string;
  title: string;
  query: string;
  turns: ChatTurn[];
  isRunning: boolean;
};

export function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [capabilities, setCapabilities] = useState<CapabilitySummary | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>([
    createChatSession("welcome", "新会话", samplePrompts[0]),
  ]);
  const [activeSessionId, setActiveSessionId] = useState("welcome");
  const stopMapRef = useRef<Map<string, () => void>>(new Map());
  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) ?? sessions[0],
    [activeSessionId, sessions],
  );
  const query = activeSession.query;
  const isRunning = activeSession.isRunning;

  useEffect(() => {
    const stops = stopMapRef.current;
    return () => {
      stops.forEach((stop) => stop());
      stops.clear();
    };
  }, []);

  useEffect(() => {
    fetchCapabilities()
      .then(setCapabilities)
      .catch(() => setCapabilities(null));
  }, []);

  function setSessionRunning(sessionId: string, running: boolean) {
    setSessions((current) =>
      updateSession(current, sessionId, (session) => ({ ...session, isRunning: running })),
    );
  }

  function stopSession(sessionId: string) {
    const stop = stopMapRef.current.get(sessionId);
    if (stop) {
      stop();
      stopMapRef.current.delete(sessionId);
    }
    setSessionRunning(sessionId, false);
  }

  function startRun(nextQuery = query) {
    const cleaned = nextQuery.trim();
    if (!cleaned) {
      return;
    }
    const runSessionId = activeSessionId;
    const turnId = crypto.randomUUID();
    const existingStop = stopMapRef.current.get(runSessionId);
    if (existingStop) {
      existingStop();
      stopMapRef.current.delete(runSessionId);
    }
    setSessions((current) =>
      updateSession(current, runSessionId, (session) => ({
        ...session,
        title: titleFromQuery(cleaned),
        query: "",
        isRunning: true,
        turns: [
          ...session.turns,
          {
            id: turnId,
            submittedQuery: cleaned,
            trace: createEmptyTrace(),
          },
        ],
      })),
    );
    const stop = runAgentTrace(
      cleaned,
      (event) => {
        setSessions((current) =>
          updateSession(current, runSessionId, (session) => ({
            ...session,
            turns: session.turns.map((turn) =>
              turn.id === turnId ? { ...turn, trace: reduceTraceEvent(turn.trace, event) } : turn,
            ),
          })),
        );
        if (event.event === "done" || event.event === "error") {
          stopMapRef.current.delete(runSessionId);
          setSessionRunning(runSessionId, false);
        }
      },
      () => {
        stopMapRef.current.delete(runSessionId);
        setSessionRunning(runSessionId, false);
      },
      "deep_research",
      runSessionId,
    );
    stopMapRef.current.set(runSessionId, stop);
  }

  function stopRun() {
    stopSession(activeSessionId);
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
    const id = crypto.randomUUID();
    setSessions((current) => [createChatSession(id, "新会话"), ...current]);
    setActiveSessionId(id);
  }

  function selectSession(id: string) {
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
          <button className="sidebar-icon" type="button" onClick={() => setSidebarOpen(false)} title="收起侧栏">
            <PanelLeftClose size={18} />
          </button>
          <button className="new-chat-button" type="button" onClick={newSession}>
            <Plus size={17} />
            <span>新会话</span>
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
        <CapabilityPanel capabilities={capabilities} />
      </aside>

      <section className="chat-shell">
        {!sidebarOpen ? (
          <button className="floating-sidebar-toggle" type="button" onClick={() => setSidebarOpen(true)} title="展开侧栏">
            <Menu size={19} />
          </button>
        ) : null}

        <section className="message-lane" aria-label="Chat">
          {activeSession.turns.length === 0 ? (
            <EmptyChat
              capabilities={capabilities}
              onPick={(prompt) => {
                updateActiveQuery(prompt);
                startRun(prompt);
              }}
            />
          ) : (
            <Conversation turns={activeSession.turns} isRunning={isRunning} />
          )}
        </section>

        <form className="composer" onSubmit={onSubmit}>
          <input
            value={query}
            onChange={(event) => updateActiveQuery(event.target.value)}
            placeholder="输入请求，按 Enter 发送"
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

function Conversation({ turns, isRunning }: { turns: ChatTurn[]; isRunning: boolean }) {
  const lastTurnId = turns.at(-1)?.id;
  return (
    <div className="conversation">
      {turns.map((turn) => (
        <ConversationTurn
          key={turn.id}
          trace={turn.trace}
          submittedQuery={turn.submittedQuery}
          isRunning={isRunning && turn.id === lastTurnId}
        />
      ))}
    </div>
  );
}

function ConversationTurn({
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
    <section className="conversation-turn">
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

          {trace.todos.length ? <TodoChecklist todos={trace.todos} /> : null}
          {trace.pendingQuestions.map((question) => (
            <QuestionPrompt key={question.questionId} question={question} />
          ))}

          {isRunning ? (
            <div className="live-row">
              <Loader2 className="spin" size={16} />
              <span>正在思考</span>
            </div>
          ) : null}

          {trace.finalText && !hasStreamedText ? <FinalCard text={trace.finalText} usage={trace.usage} /> : null}
          {trace.status === "completed" && hasStreamedText && trace.usage ? <CompletionCard usage={trace.usage} /> : null}
          {trace.error || trace.errorPayload ? (
            <ErrorCard message={trace.error ?? ""} payload={trace.errorPayload} />
          ) : null}
        </div>
      </article>
    </section>
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
        {friendly.hint ? <p className="error-hint">提示：{friendly.hint}</p> : null}
        {payload?.code ? (
          <p className="error-meta">
            code: {payload.code}
            {payload.category ? ` / category: ${payload.category}` : ""}
            {friendly.retryable ? " / 可重试" : ""}
          </p>
        ) : null}
      </div>
    </article>
  );
}

function TodoChecklist({ todos }: { todos: TodoItem[] }) {
  const completed = todos.filter((todo) => todo.status === "completed").length;
  return (
    <article className="todo-checklist" aria-label="Agent todo list">
      <header className="todo-checklist-head">
        <span>任务清单</span>
        <span className="todo-progress">
          {completed}/{todos.length}
        </span>
      </header>
      <ul>
        {todos.map((todo, index) => (
          <li key={`${todo.content}-${index}`} className={`todo-item ${todo.status}`}>
            <span className={`todo-mark ${todo.status}`} aria-hidden="true" />
            <span className="todo-text">
              {todo.status === "in_progress" ? todo.activeForm : todo.content}
            </span>
          </li>
        ))}
      </ul>
    </article>
  );
}

function QuestionPrompt({ question }: { question: UserQuestion }) {
  return (
    <article className="question-prompt" aria-label="Agent question">
      <header className="question-prompt-head">
        <span>需要你的输入</span>
      </header>
      <p className="question-text">{question.question}</p>
      {question.options.length ? (
        <ul className="question-options">
          {question.options.map((option) => (
            <li key={option}>{option}</li>
          ))}
        </ul>
      ) : null}
      <p className="question-hint">
        在下一条消息里回答即可，agent 会从下一轮接着处理。
      </p>
    </article>
  );
}

function EmptyChat({
  capabilities,
  onPick,
}: {
  capabilities: CapabilitySummary | null;
  onPick: (prompt: string) => void;
}) {
  return (
    <div className="empty-chat">
      <div className="empty-mark">
        <Sparkles size={22} />
      </div>
      <h1>AgentEngine</h1>
      <CapabilityStats capabilities={capabilities} />
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

function CapabilityPanel({ capabilities }: { capabilities: CapabilitySummary | null }) {
  const toolCount = capabilities?.tools.length ?? 0;
  const skillCount = capabilities?.skills.length ?? 0;
  return (
    <section className="capability-panel">
      <div className="capability-panel-head">
        <Wrench size={15} />
        <span>能力</span>
      </div>
      <p>
        {toolCount} tools / {skillCount} skills
      </p>
      <CapabilityMiniList title="Tools" items={capabilities?.tools} />
      <CapabilityMiniList title="Skills" items={capabilities?.skills} emptyText="未发现本地 skill" />
    </section>
  );
}

function CapabilityStats({ capabilities }: { capabilities: CapabilitySummary | null }) {
  if (!capabilities) {
    return null;
  }
  return (
    <div className="capability-stats">
      <span>{capabilities.agents.length} agents</span>
      <span>{capabilities.tools.length} tools</span>
      <span>{capabilities.skills.length} skills</span>
    </div>
  );
}

function CapabilityMiniList({
  title,
  items,
  emptyText = "暂无",
}: {
  title: string;
  items?: { name: string; description: string }[];
  emptyText?: string;
}) {
  const visible = items?.slice(0, 4) ?? [];
  return (
    <div className="capability-mini-list">
      <strong>{title}</strong>
      {visible.length ? (
        visible.map((item) => (
          <span key={item.name} title={item.description}>
            {item.name}
          </span>
        ))
      ) : (
        <em>{emptyText}</em>
      )}
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
    turns: [],
    isRunning: false,
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
