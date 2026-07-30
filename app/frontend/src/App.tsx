import { ReactNode, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Clock3,
  Copy,
  FileText,
  Menu,
  Loader2,
  Pencil,
  Send,
  Settings2,
  Sparkles,
  X,
  Wrench,
} from "lucide-react";
import { ArtifactPanel } from "./components/ArtifactPanel";
import {
  Composer,
  detectFileKind,
  type ComposerFile,
  type ComposerSkill,
  type ComposerSkillOption,
  type ThinkingMode,
} from "./components/Composer";
import { SkillsManager } from "./components/SkillsManager";
import { AppNav, type AppView, type NavSpace } from "./components/AppNav";
import { SkillsPage } from "./components/SkillsPage";
import { ExpertsPage } from "./components/ExpertsPage";
import { ConnectorsPage } from "./components/ConnectorsPage";
import { createEmptyTrace, reduceTraceEvent } from "./traceReducer";
import {
  fetchCapabilities,
  fetchSkills,
  runAgentTrace,
  runReportTrace,
  uploadReportFile,
} from "./traceTransport";
import { translateError } from "./friendlyErrors";
import type {
  CapabilitySummary,
  ErrorPayload,
  Expert,
  ExpertTeam,
  ReportFileSummary,
  RunTrace,
  SkillSummary,
  StepTrace,
  TodoItem,
  ToolTrace,
  UsageSummary,
  UserQuestion,
  ArtifactTrace,
} from "./types";

const samplePrompts = [
  "阅读 README.md 和 docs/API.md，给我一份项目概览",
  "用 read_file 看一下前端入口在哪里",
  "解释 Web 流式事件协议",
];

type ChatTurn = {
  id: string;
  submittedQuery: string;
  createdAt: number;
  files: ReportFileSummary[];
  trace: RunTrace;
  // For expert-team runs: a label like "成员 1/2 · 股票研究专家" shown above the turn.
  memberLabel?: string;
};

type TeamMember = { name: string; role: string };

type ChatSession = {
  id: string;
  title: string;
  query: string;
  turns: ChatTurn[];
  isRunning: boolean;
  // The agent/expert this session runs with. Defaults to deep_research to
  // preserve the previous hard-coded behaviour; set when starting from an expert.
  agentName?: string;
  expertRole?: string;
  // For expert-team sessions: the team name and its ordered members. When set,
  // submitting a message runs the members as a sequential relay pipeline.
  teamName?: string;
  teamMembers?: TeamMember[];
};

const ARTIFACT_WIDTH_MIN = 480;
const ARTIFACT_WIDTH_MAX = 1040;
const ARTIFACT_WIDTH_DEFAULT = 720;
const CHAT_SESSIONS_STORAGE_KEY = "agentengine.web.sessions.v1";
const ACTIVE_SESSION_STORAGE_KEY = "agentengine.web.activeSessionId.v1";
const MAX_PERSISTED_SESSIONS = 30;

export function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [capabilities, setCapabilities] = useState<CapabilitySummary | null>(null);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [showSkillsManager, setShowSkillsManager] = useState(false);
  const [activeView, setActiveView] = useState<AppView>("chat");
  const [closedArtifactId, setClosedArtifactId] = useState<string | null>(null);
  const [reportFiles, setReportFiles] = useState<ReportFileSummary[]>([]);
  const [previewFile, setPreviewFile] = useState<ReportFileSummary | null>(null);
  const [imagePreviews, setImagePreviews] = useState<Record<string, string>>({});
  const [selectedSkill, setSelectedSkill] = useState<ComposerSkill>("chat");
  const [thinkingMode, setThinkingMode] = useState<ThinkingMode>(() => {
    const saved = localStorage.getItem("thinkingMode");
    return saved === "fast" || saved === "auto" ? saved : "fast";
  });
  useEffect(() => {
    localStorage.setItem("thinkingMode", thinkingMode);
  }, [thinkingMode]);
  const [composerUploading, setComposerUploading] = useState(false);
  const [composerUploadError, setComposerUploadError] = useState("");
  const [artifactWidth, setArtifactWidth] = useState(getStoredArtifactWidth);
  const [sessions, setSessions] = useState<ChatSession[]>(loadPersistedSessions);
  const [activeSessionId, setActiveSessionId] = useState(() => loadPersistedActiveSessionId(sessions));
  const stopMapRef = useRef<Map<string, () => void>>(new Map());
  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) ?? sessions[0],
    [activeSessionId, sessions],
  );
  const query = activeSession.query;
  const isRunning = activeSession.isRunning;
  const activeArtifact = useMemo(() => {
    const artifacts = activeSession.turns.flatMap((turn) => turn.trace.artifacts);
    // Prefer the most recent artifact that actually has content. While a
    // new turn is spinning up (artifact_start fired but no html_delta
    // yet) the panel keeps showing the previous, ready artifact instead
    // of going blank — that's the "don't auto-close the right side"
    // behaviour the user asked for.
    const withOutput = artifacts.filter((artifact) =>
      artifact.html.trim().length > 0
      || artifact.status === "ready"
      || artifact.status === "failed"
      || Object.keys(artifact.exports ?? {}).length > 0
      || Boolean(artifact.error),
    );
    return withOutput.at(-1) ?? artifacts.at(-1);
  }, [activeSession.turns]);
  const artifactHasOutput = Boolean(
    activeArtifact
      && (
        activeArtifact.html.trim().length > 0
        || activeArtifact.status === "ready"
        || activeArtifact.status === "failed"
        || Object.keys(activeArtifact.exports ?? {}).length > 0
        || activeArtifact.error
      ),
  );
  const visibleArtifact = previewFile
    ? null
    : activeArtifact?.id === closedArtifactId || !artifactHasOutput
      ? null
      : activeArtifact;
  const hasRightPanel = activeView === "chat" && Boolean(visibleArtifact || previewFile);
  const appClassName = [
    "app-layout",
    sidebarOpen ? "" : "sidebar-collapsed",
    hasRightPanel ? "has-artifact" : "",
  ].filter(Boolean).join(" ");
  const appStyle = hasRightPanel
    ? ({ "--artifact-width": `${artifactWidth}px` } as CSSProperties)
    : undefined;

  useEffect(() => {
    const stops = stopMapRef.current;
    return () => {
      stops.forEach((stop) => stop());
      stops.clear();
    };
  }, []);

  useEffect(() => {
    return () => {
      Object.values(imagePreviews).forEach((url) => URL.revokeObjectURL(url));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchCapabilities()
      .then(setCapabilities)
      .catch(() => setCapabilities(null));
    refreshSkills();
  }, []);

  function refreshSkills() {
    fetchSkills()
      .then(setSkills)
      .catch(() => setSkills([]));
  }

  function handleSkillsChanged() {
    refreshSkills();
    // Enable-state changes affect which skills the agent can use, so keep the
    // sidebar capability counts in sync.
    fetchCapabilities()
      .then(setCapabilities)
      .catch(() => undefined);
  }

  const enabledSkillOptions = useMemo<ComposerSkillOption[]>(
    () =>
      skills
        .filter((skill) => skill.enabled)
        .map((skill) => ({ name: skill.name, description: skill.description })),
    [skills],
  );

  // If the selected skill gets disabled or removed, fall back to plain chat so
  // the composer never shows a dangling selection.
  useEffect(() => {
    if (selectedSkill === "chat") {
      return;
    }
    if (skills.length && !enabledSkillOptions.some((option) => option.name === selectedSkill)) {
      setSelectedSkill("chat");
    }
  }, [enabledSkillOptions, selectedSkill, skills.length]);

  useEffect(() => {
    localStorage.setItem("artifactWidth", String(artifactWidth));
  }, [artifactWidth]);

  useEffect(() => {
    persistSessions(sessions, activeSessionId);
  }, [sessions, activeSessionId]);

  useEffect(() => {
    if (sessions.some((session) => session.id === activeSessionId)) {
      return;
    }
    setActiveSessionId(sessions[0]?.id ?? "welcome");
  }, [sessions, activeSessionId]);

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

  function streamAgentRun({
    sessionId,
    turnId,
    effectiveQuery,
    agentName,
    skill,
  }: {
    sessionId: string;
    turnId: string;
    effectiveQuery: string;
    agentName: string;
    skill: string;
  }) {
    const stop = runAgentTrace(
      effectiveQuery,
      (event) => {
        setSessions((current) =>
          updateSession(current, sessionId, (session) => ({
            ...session,
            turns: session.turns.map((turn) =>
              turn.id === turnId ? { ...turn, trace: reduceTraceEvent(turn.trace, event) } : turn,
            ),
          })),
        );
        if (event.event === "done" || event.event === "error") {
          stopMapRef.current.delete(sessionId);
          setSessionRunning(sessionId, false);
        }
      },
      () => {
        stopMapRef.current.delete(sessionId);
        setSessionRunning(sessionId, false);
      },
      agentName,
      sessionId,
      skill,
    );
    stopMapRef.current.set(sessionId, stop);
  }

  function startRun(nextQuery = query) {
    const cleaned = nextQuery.trim();
    if (!cleaned) {
      return;
    }
    const attachedFiles = reportFiles;
    const effectiveQuery = withUploadedFileContext(cleaned, attachedFiles);
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
            createdAt: Date.now(),
            files: attachedFiles,
            trace: createEmptyTrace(),
          },
        ],
      })),
    );
    setReportFiles([]);
    setComposerUploadError("");
    setPreviewFile(null);
    streamAgentRun({
      sessionId: runSessionId,
      turnId,
      effectiveQuery,
      agentName: activeSession.agentName ?? "general_chat",
      skill: selectedSkill,
    });
  }

  function resendEditedLastTurn(turnId: string, nextQuery: string) {
    const cleaned = nextQuery.trim();
    const lastTurn = activeSession.turns.at(-1);
    if (
      !cleaned
      || activeSession.isRunning
      || activeSession.teamMembers?.length
      || !lastTurn
      || lastTurn.id !== turnId
    ) {
      return;
    }
    const effectiveQuery = withUploadedFileContext(cleaned, lastTurn.files);
    setPreviewFile(null);
    setSessions((current) =>
      updateSession(current, activeSessionId, (session) => ({
        ...session,
        title: titleFromQuery(cleaned),
        query: "",
        isRunning: true,
        turns: session.turns.map((turn) =>
          turn.id === turnId
            ? { ...turn, submittedQuery: cleaned, trace: createEmptyTrace() }
            : turn,
        ),
      })),
    );
    streamAgentRun({
      sessionId: activeSessionId,
      turnId,
      effectiveQuery,
      agentName: activeSession.agentName ?? "general_chat",
      skill: selectedSkill,
    });
  }

  function startExpertChat(expert: Expert) {
    const id = crypto.randomUUID();
    setSessions((current) => [
      {
        ...createChatSession(id, expert.role),
        agentName: expert.name,
        expertRole: expert.role,
      },
      ...current,
    ]);
    setActiveSessionId(id);
    setSelectedSkill("chat");
    setReportFiles([]);
    setComposerUploadError("");
    setPreviewFile(null);
    setClosedArtifactId(null);
    setActiveView("chat");
  }

  function startTeamChat(team: ExpertTeam) {
    const id = crypto.randomUUID();
    const members: TeamMember[] = team.member_experts.map((expert) => ({
      name: expert.name,
      role: expert.role,
    }));
    setSessions((current) => [
      {
        ...createChatSession(id, team.name),
        teamName: team.name,
        teamMembers: members,
      },
      ...current,
    ]);
    setActiveSessionId(id);
    setSelectedSkill("chat");
    setReportFiles([]);
    setComposerUploadError("");
    setPreviewFile(null);
    setClosedArtifactId(null);
    setActiveView("chat");
  }

  // Run a team's members as a sequential relay: each member is its own turn,
  // and the previous member's answer is threaded into the next member's query.
  // Built entirely on the single-agent runAgentTrace — no engine changes.
  function runTeamPipeline(sessionId: string, userQuery: string, members: TeamMember[]) {
    setSessionRunning(sessionId, true);

    const runMember = (index: number, priorOutput: string, priorRole: string) => {
      if (index >= members.length) {
        stopMapRef.current.delete(sessionId);
        setSessionRunning(sessionId, false);
        return;
      }
      const member = members[index];
      const turnId = crypto.randomUUID();
      const memberLabel = `成员 ${index + 1}/${members.length} · ${member.role}`;
      const memberQuery =
        index === 0
          ? userQuery
          : `下面是「${priorRole}」给出的分析：\n\n${priorOutput}\n\n` +
            `请你作为「${member.role}」在此基础上继续，针对原始诉求给出你的专业意见：${userQuery}`;

      // Each member is a fresh turn (own trace) — sidesteps the start-reset
      // behaviour that would otherwise wipe earlier members' output.
      setSessions((current) =>
        updateSession(current, sessionId, (session) => ({
          ...session,
          turns: [
            ...session.turns,
            {
              id: turnId,
              submittedQuery: index === 0 ? userQuery : `（接力）${member.role}`,
              createdAt: Date.now(),
              files: [],
              trace: createEmptyTrace(),
              memberLabel,
            },
          ],
        })),
      );

      let latestTrace = createEmptyTrace();
      const stop = runAgentTrace(
        memberQuery,
        (event) => {
          setSessions((current) =>
            updateSession(current, sessionId, (session) => ({
              ...session,
              turns: session.turns.map((turn) => {
                if (turn.id !== turnId) {
                  return turn;
                }
                latestTrace = reduceTraceEvent(turn.trace, event);
                return { ...turn, trace: latestTrace };
              }),
            })),
          );
        },
        () => {
          // Member finished: thread its output into the next member.
          const output = extractTraceText(latestTrace);
          runMember(index + 1, output, member.role);
        },
        member.name,
        sessionId,
        "chat",
      );
      stopMapRef.current.set(sessionId, stop);
    };

    runMember(0, "", "");
  }

  function startReportDemo(nextIntent = query) {
    const cleanedIntent = nextIntent.trim();
    const runSessionId = activeSessionId;
    const turnId = crypto.randomUUID();
    const attachedFiles = reportFiles;
    const fileIds = attachedFiles.map((file) => file.id);
    // If the previous artifact in this session has html (regardless of
    // status), let the backend resume from it when the user's intent
    // looks like "continue / iterate / refine" so we don't throw away
    // the work already done.
    const RESUME_KEYWORDS = ["继续", "接着", "补全", "修改", "调整", "增加", "去掉", "换成", "再做一版", "改一下", "retry", "继续生成"];
    const wantsResume = cleanedIntent !== ""
      && RESUME_KEYWORDS.some((kw) => cleanedIntent.toLowerCase().includes(kw.toLowerCase()));
    const priorArtifact = wantsResume
      ? activeSession.turns
          .flatMap((turn) => turn.trace.artifacts)
          .filter((artifact) => artifact.html.trim().length > 0)
          .at(-1)
      : undefined;
    const resumeFromReportId = priorArtifact?.reportId ?? "";
    const title = attachedFiles.length ? "数据分析" : "数据分析 Demo";
    const intent = cleanedIntent || (attachedFiles.length
      ? "基于已上传的文件生成结构化数据分析报告。"
      : "生成一个用于验证 artifact 分屏、报告 IR 和 Markdown 导出的数据分析演示。");
    const existingStop = stopMapRef.current.get(runSessionId);
    if (existingStop) {
      existingStop();
      stopMapRef.current.delete(runSessionId);
    }
    // Don't reset closedArtifactId here: each new artifact has its own
    // uuid, so visibility is controlled by the per-artifact id match.
    // Wiping closedArtifactId would re-open an artifact the user just
    // explicitly closed.
    setPreviewFile(null);
    setSessions((current) =>
      updateSession(current, runSessionId, (session) => ({
        ...session,
        title,
        query: "",
        isRunning: true,
        turns: [
          ...session.turns,
          {
            id: turnId,
            submittedQuery: cleanedIntent || title,
            createdAt: Date.now(),
            files: attachedFiles,
            trace: createEmptyTrace(),
          },
        ],
      })),
    );
    setReportFiles([]);
    setComposerUploadError("");
    const stop = runReportTrace(
      title,
      intent,
      fileIds,
      selectedSkill,
      (event) => {
        setSessions((current) =>
          updateSession(current, runSessionId, (session) => ({
            ...session,
            turns: session.turns.map((turn) =>
              turn.id === turnId ? { ...turn, trace: reduceTraceEvent(turn.trace, event) } : turn,
            ),
          })),
        );
        if (event.event === "artifact_ready" || event.event === "artifact_error" || event.event === "error") {
          stopMapRef.current.delete(runSessionId);
          setSessionRunning(runSessionId, false);
        }
      },
      () => {
        stopMapRef.current.delete(runSessionId);
        setSessionRunning(runSessionId, false);
      },
      runSessionId,
      resumeFromReportId,
    );
    stopMapRef.current.set(runSessionId, stop);
  }

  async function uploadFilesFromComposer(files: FileList | File[]) {
    const selectedFiles = Array.from(files).filter((file) => file.name.trim());
    if (!selectedFiles.length) {
      return;
    }
    setComposerUploading(true);
    setComposerUploadError("");
    const localPreviews: Array<{ filename: string; url: string }> = [];
    for (const file of selectedFiles) {
      if (file.type.startsWith("image/")) {
        localPreviews.push({ filename: file.name, url: URL.createObjectURL(file) });
      }
    }
    try {
      const uploaded = await Promise.all(
        selectedFiles.map((file) => uploadReportFile(file, activeSessionId)),
      );
      setReportFiles((current) => [...current, ...uploaded]);
      if (localPreviews.length) {
        setImagePreviews((current) => {
          const next = { ...current };
          uploaded.forEach((record) => {
            const match = localPreviews.find((preview) => preview.filename === record.filename);
            if (match) {
              next[record.id] = match.url;
            }
          });
          return next;
        });
      }
    } catch (err) {
      localPreviews.forEach((preview) => URL.revokeObjectURL(preview.url));
      setComposerUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setComposerUploading(false);
    }
  }

  function removeComposerFile(id: string) {
    setReportFiles((current) => current.filter((file) => file.id !== id));
    setImagePreviews((current) => {
      if (!current[id]) {
        return current;
      }
      URL.revokeObjectURL(current[id]);
      const next = { ...current };
      delete next[id];
      return next;
    });
  }

  function stopRun() {
    stopSession(activeSessionId);
  }

  function submitFromComposer(nextQuery: string) {
    if (isRunning) {
      stopRun();
      return;
    }
    // Expert-team session: run the members as a sequential relay pipeline.
    if (activeSession.teamMembers && activeSession.teamMembers.length > 0) {
      const cleaned = nextQuery.trim();
      if (!cleaned) {
        return;
      }
      updateActiveQuery("");
      runTeamPipeline(activeSessionId, cleaned, activeSession.teamMembers);
      return;
    }
    if (selectedSkill === "data_analysis") {
      startReportDemo(nextQuery);
      return;
    }
    startRun(nextQuery);
  }

  function newSession() {
    const id = crypto.randomUUID();
    setSessions((current) => [createChatSession(id, "新会话"), ...current]);
    setActiveSessionId(id);
    setSelectedSkill("chat");
    setReportFiles([]);
    setComposerUploadError("");
    setPreviewFile(null);
    setClosedArtifactId(null);
  }

  function selectSession(id: string) {
    setActiveSessionId(id);
    setPreviewFile(null);
    setClosedArtifactId(null);
  }

  function deleteSession(id: string) {
    const stop = stopMapRef.current.get(id);
    if (stop) {
      stop();
      stopMapRef.current.delete(id);
    }
    const deleteIndex = sessions.findIndex((session) => session.id === id);
    const remainingSessions = sessions.filter((session) => session.id !== id);
    const nextSessions = remainingSessions.length
      ? remainingSessions
      : [createChatSession(crypto.randomUUID(), "新会话")];
    const nextActiveSessionId = activeSessionId === id
      ? nextSessions[Math.max(0, Math.min(deleteIndex, nextSessions.length - 1))].id
      : activeSessionId;

    setSessions(nextSessions);
    setActiveSessionId(nextActiveSessionId);
    if (activeSessionId === id) {
      setSelectedSkill("chat");
      setReportFiles([]);
      setComposerUploadError("");
      setPreviewFile(null);
      setClosedArtifactId(null);
    }
  }

  function updateActiveQuery(value: string) {
    setSessions((current) =>
      updateSession(current, activeSessionId, (session) => ({
        ...session,
        query: value,
      })),
    );
  }

  function startArtifactResize(event: ReactPointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = artifactWidth;
    document.body.classList.add("is-resizing-artifact");

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const delta = startX - moveEvent.clientX;
      setArtifactWidth(clampArtifactWidth(startWidth + delta, sidebarOpen));
    };

    const handlePointerUp = () => {
      document.body.classList.remove("is-resizing-artifact");
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp, { once: true });
  }

  const canSubmit = selectedSkill === "data_analysis"
    ? Boolean(query.trim() || reportFiles.length)
    : Boolean(query.trim());

  const composerFiles = useMemo<ComposerFile[]>(
    () =>
      reportFiles.map((file) => ({
        id: file.id,
        filename: file.filename,
        size_bytes: file.size_bytes,
        kind: detectFileKind(file.filename),
        previewUrl: imagePreviews[file.id],
      })),
    [reportFiles, imagePreviews],
  );

  const composerProps = {
    query,
    onQueryChange: updateActiveQuery,
    onSubmit: submitFromComposer,
    isRunning,
    onStop: stopRun,
    skill: selectedSkill,
    onSkillChange: setSelectedSkill,
    skillOptions: enabledSkillOptions,
    onManageSkills: () => setShowSkillsManager(true),
    thinkingMode,
    onThinkingModeChange: setThinkingMode,
    files: composerFiles,
    onAttach: (files: FileList | File[]) => {
      void uploadFilesFromComposer(files);
    },
    onRemoveFile: removeComposerFile,
    uploading: composerUploading,
    uploadError: composerUploadError,
    canSubmit,
  };

  const navSpaces = useMemo<NavSpace[]>(
    () => [
      { id: "agentengine", name: "AgentEngine", sessionIds: [] },
    ],
    [],
  );

  function navigate(view: AppView) {
    setActiveView(view);
    if (view !== "chat") {
      setPreviewFile(null);
    }
  }

  return (
    <main className={appClassName} style={appStyle}>
      {sidebarOpen ? (
        <AppNav
          version="0.2.0"
          activeView={activeView}
          onNavigate={navigate}
          onNewTask={() => {
            navigate("chat");
            newSession();
          }}
          onCollapse={() => setSidebarOpen(false)}
          sessions={sessions.map((session) => ({ id: session.id, title: session.title }))}
          spaces={navSpaces}
          activeSessionId={activeSessionId}
          onSelectSession={(id) => {
            navigate("chat");
            selectSession(id);
          }}
          onDeleteSession={deleteSession}
          onOpenSettings={() => navigate("skills")}
          onLogout={() => {
            setActiveSessionId("");
            setSessions([]);
            setSidebarOpen(false);
          }}
        />
      ) : null}

      {activeView === "chat" ? (
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
                expertRole={activeSession.expertRole}
                teamName={activeSession.teamName}
                teamMembers={activeSession.teamMembers}
                composer={<Composer variant="center" {...composerProps} />}
                onPick={(prompt) => {
                  if (activeSession.teamMembers && activeSession.teamMembers.length > 0) {
                    runTeamPipeline(activeSessionId, prompt, activeSession.teamMembers);
                    return;
                  }
                  updateActiveQuery(prompt);
                  startRun(prompt);
                }}
              />
            ) : (
              <Conversation
                turns={activeSession.turns}
                isRunning={isRunning}
                canEditLastTurn={!activeSession.teamMembers?.length}
                onEditLastTurn={resendEditedLastTurn}
                onOpenArtifact={() => {
                  setPreviewFile(null);
                  setClosedArtifactId(null);
                }}
                onPreviewFile={setPreviewFile}
              />
            )}
          </section>

          {activeSession.turns.length > 0 ? (
            <div className="composer-dock">
              <Composer variant="docked" {...composerProps} />
            </div>
          ) : null}
        </section>
      ) : activeView === "skills" ? (
        <SkillsPage
          skills={skills}
          onNavigate={navigate}
          onSkillsChanged={handleSkillsChanged}
          onOpenImport={() => setShowSkillsManager(true)}
          sidebarOpen={sidebarOpen}
          onExpandSidebar={() => setSidebarOpen(true)}
        />
      ) : activeView === "experts" ? (
        <ExpertsPage
          onNavigate={navigate}
          onStartExpert={startExpertChat}
          onStartTeam={startTeamChat}
          sidebarOpen={sidebarOpen}
          onExpandSidebar={() => setSidebarOpen(true)}
        />
      ) : activeView === "connectors" ? (
        <ConnectorsPage />
      ) : (
        <PlaceholderView
          view={activeView}
          sidebarOpen={sidebarOpen}
          onExpandSidebar={() => setSidebarOpen(true)}
        />
      )}

      {activeView === "chat" && hasRightPanel ? (
        <>
          <button
            className="artifact-resizer"
            type="button"
            aria-label="调整报告预览宽度"
            title="拖动调整报告预览宽度"
            onPointerDown={startArtifactResize}
          />
          {previewFile ? (
            <FilePreviewPanel
              file={previewFile}
              previewUrl={imagePreviews[previewFile.id]}
              onClose={() => setPreviewFile(null)}
            />
          ) : visibleArtifact ? (
            <ArtifactPanel
              artifact={visibleArtifact}
              onClose={() => setClosedArtifactId(visibleArtifact.id)}
            />
          ) : null}
        </>
      ) : null}

      {showSkillsManager ? (
        <SkillsManager
          skills={skills}
          onClose={() => setShowSkillsManager(false)}
          onChanged={handleSkillsChanged}
        />
      ) : null}
    </main>
  );
}

function clampArtifactWidth(value: number, sidebarVisible = true) {
  const reservedWidth = sidebarVisible ? 628 : 368;
  const viewportMax = typeof window === "undefined"
    ? ARTIFACT_WIDTH_MAX
    : Math.max(ARTIFACT_WIDTH_MIN, window.innerWidth - reservedWidth);
  return Math.min(Math.max(value, ARTIFACT_WIDTH_MIN), Math.min(ARTIFACT_WIDTH_MAX, viewportMax));
}

function getStoredArtifactWidth() {
  const stored = Number(localStorage.getItem("artifactWidth"));
  return clampArtifactWidth(Number.isFinite(stored) && stored > 0 ? stored : ARTIFACT_WIDTH_DEFAULT);
}

function Conversation({
  turns,
  isRunning,
  canEditLastTurn,
  onEditLastTurn,
  onOpenArtifact,
  onPreviewFile,
}: {
  turns: ChatTurn[];
  isRunning: boolean;
  canEditLastTurn: boolean;
  onEditLastTurn: (turnId: string, nextQuery: string) => void;
  onOpenArtifact: () => void;
  onPreviewFile: (file: ReportFileSummary) => void;
}) {
  const lastTurnId = turns.at(-1)?.id;
  return (
    <div className="conversation">
      {turns.map((turn) => (
        <ConversationTurn
          key={turn.id}
          turnId={turn.id}
          trace={turn.trace}
          submittedQuery={turn.submittedQuery}
          createdAt={turn.createdAt}
          files={turn.files}
          memberLabel={turn.memberLabel}
          isRunning={isRunning && turn.id === lastTurnId}
          canEdit={canEditLastTurn && turn.id === lastTurnId && !isRunning}
          onEdit={onEditLastTurn}
          onOpenArtifact={onOpenArtifact}
          onPreviewFile={onPreviewFile}
        />
      ))}
    </div>
  );
}

function ConversationTurn({
  turnId,
  trace,
  submittedQuery,
  createdAt,
  files,
  memberLabel,
  isRunning,
  canEdit,
  onEdit,
  onOpenArtifact,
  onPreviewFile,
}: {
  turnId: string;
  trace: RunTrace;
  submittedQuery: string;
  createdAt: number;
  files: ReportFileSummary[];
  memberLabel?: string;
  isRunning: boolean;
  canEdit: boolean;
  onEdit: (turnId: string, nextQuery: string) => void;
  onOpenArtifact: () => void;
  onPreviewFile: (file: ReportFileSummary) => void;
}) {
  const hasStreamedText = trace.steps.some((step) => step.text.join("").trim().length > 0);
  const messageText = submittedQuery || trace.query;
  const [editingMessage, setEditingMessage] = useState(false);
  const [draftMessage, setDraftMessage] = useState(messageText);

  function copyMessage() {
    void navigator.clipboard.writeText(messageText).catch(() => undefined);
  }

  function beginEdit() {
    setDraftMessage(messageText);
    setEditingMessage(true);
  }

  function submitEdit() {
    const nextMessage = draftMessage.trim();
    if (!nextMessage) {
      return;
    }
    setEditingMessage(false);
    onEdit(turnId, nextMessage);
  }

  return (
    <section className="conversation-turn">
      {memberLabel ? (
        <div className="team-member-label">
          <Sparkles size={13} />
          <span>{memberLabel}</span>
        </div>
      ) : null}
      <article className="message-row user-row">
        <div className="user-message">
          {editingMessage ? (
            <div className="user-edit-box">
              <textarea
                value={draftMessage}
                onChange={(event) => setDraftMessage(event.target.value)}
                aria-label="Edit message"
                rows={3}
                autoFocus
              />
              <div className="user-edit-actions">
                <button type="button" onClick={() => setEditingMessage(false)}>Cancel</button>
                <button type="button" onClick={submitEdit} disabled={!draftMessage.trim()}>
                  <Send size={14} />
                  Send
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="user-bubble">{messageText}</div>
              <div className="user-message-actions" aria-label="Message actions">
                {canEdit ? <time dateTime={new Date(createdAt).toISOString()}>{formatMessageTime(createdAt)}</time> : null}
                <button type="button" onClick={copyMessage} title="Copy message" aria-label="Copy message">
                  <Copy size={14} />
                </button>
                {canEdit ? (
                  <button type="button" onClick={beginEdit} title="Edit message" aria-label="Edit message">
                    <Pencil size={14} />
                  </button>
                ) : null}
              </div>
            </>
          )}
          {files.length ? <MessageFileList files={files} onPreview={onPreviewFile} /> : null}
        </div>
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
          {trace.artifacts
            // Skip artifacts that haven't received any streamed content yet.
            // Backend emits ``artifact_start`` immediately; we wait for the
            // first ``artifact_html_delta`` (or a terminal failure) before
            // surfacing the card, so users never see an empty "Streaming"
            // placeholder.
            .filter((artifact) => artifact.html.length > 0 || artifact.status === "failed")
            .map((artifact, index) => (
              <ArtifactCard
                key={artifact.id}
                artifact={artifact}
                onOpen={onOpenArtifact}
                isFirst={index === 0}
                hasNoStepText={!hasStreamedText}
              />
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

function MessageFileList({
  files,
  onPreview,
}: {
  files: ReportFileSummary[];
  onPreview: (file: ReportFileSummary) => void;
}) {
  return (
    <div className="message-file-list" aria-label="Submitted files">
      {files.map((file) => (
        <button
          className="message-file-chip"
          key={file.id}
          type="button"
          onClick={() => onPreview(file)}
          title={file.filename}
        >
          <FileText size={16} />
          <span>
            <strong>{file.filename}</strong>
            <em>{fileKindLabel(file)}</em>
          </span>
        </button>
      ))}
    </div>
  );
}

function FilePreviewPanel({
  file,
  previewUrl,
  onClose,
}: {
  file: ReportFileSummary;
  previewUrl?: string;
  onClose: () => void;
}) {
  const parsed = file.parsed;
  const firstSheet = parsed.sheets[0];

  return (
    <aside className="artifact-panel file-preview-panel" aria-label="File preview">
      <header className="artifact-header">
        <div className="artifact-title">
          <FileText size={18} />
          <div>
            <strong>{file.filename}</strong>
            <span>{fileKindLabel(file)}</span>
          </div>
        </div>
        <button className="artifact-icon-button" type="button" title="Close preview" onClick={onClose}>
          <X size={17} />
        </button>
      </header>

      <div className="file-preview-scroll">
        <section className="file-preview-meta" aria-label="File metadata">
          <span>
            <strong>Type</strong>
            {parsed.extension || "unknown"}
          </span>
          <span>
            <strong>Size</strong>
            {formatBytes(file.size_bytes)}
          </span>
          {parsed.page_count ? (
            <span>
              <strong>Pages</strong>
              {parsed.page_count}
            </span>
          ) : null}
          {parsed.sheets.length ? (
            <span>
              <strong>Sheets</strong>
              {parsed.sheets.length}
            </span>
          ) : null}
        </section>

        {previewUrl ? (
          <section className="file-preview-section">
            <h2>Image Preview</h2>
            <div className="file-preview-image-wrap">
              <img src={previewUrl} alt={file.filename} />
            </div>
          </section>
        ) : null}

        {parsed.text_preview ? (
          <section className="file-preview-section">
            <h2>Text Preview</h2>
            <pre className="file-preview-text">{parsed.text_preview}</pre>
          </section>
        ) : null}

        {firstSheet ? <FileSheetPreview sheet={firstSheet} /> : null}

        {parsed.warnings.length ? (
          <section className="file-preview-section">
            <h2>Warnings</h2>
            <ul className="file-preview-warnings">
              {parsed.warnings.map((warning, index) => (
                <li key={`${warning}-${index}`}>{warning}</li>
              ))}
            </ul>
          </section>
        ) : null}

        {!previewUrl && !parsed.text_preview && !firstSheet ? (
          <div className="file-preview-empty">
            <FileText size={24} />
            <span>No parsed preview is available for this file.</span>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

function FileSheetPreview({ sheet }: { sheet: ReportFileSummary["parsed"]["sheets"][number] }) {
  const rows = sheet.rows.slice(0, 20);

  return (
    <section className="file-preview-section">
      <div className="file-preview-section-head">
        <h2>{sheet.name}</h2>
        <span>
          {sheet.row_count} rows / {sheet.column_count} columns
        </span>
      </div>
      <div className="file-preview-table-wrap">
        <table className="file-preview-sheet">
          <thead>
            <tr>
              {sheet.headers.map((header, index) => (
                <th key={`${header}-${index}`}>{header || `Column ${index + 1}`}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={`row-${rowIndex}`}>
                {sheet.headers.map((_, columnIndex) => (
                  <td key={`cell-${rowIndex}-${columnIndex}`}>{row[columnIndex] ?? ""}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ArtifactCard({ artifact, onOpen, isFirst = false, hasNoStepText = false }: { artifact: ArtifactTrace; onOpen: () => void; isFirst?: boolean; hasNoStepText?: boolean }) {
  const exportCount = Object.keys(artifact.exports ?? {}).length;
  const subtitle = artifact.status === "ready"
    ? `已生成 HTML 报告，${exportCount} 个导出入口`
    : artifact.currentSection
      ? `正在输出：${artifact.currentSection}`
      : `正在渲染 HTML${artifact.html ? `（${artifact.html.length} 字符）` : ""}`;

  return (
    <>
      {isFirst && hasNoStepText ? (
        <p className="artifact-context-label">
          {artifact.status === "ready" ? "报告已生成，点击在右侧预览：" : "正在生成报告，稍后可在右侧预览："}
        </p>
      ) : null}
      <button className="artifact-card" type="button" onClick={onOpen}>
        <span className="artifact-card-icon">
          <FileText size={18} />
        </span>
        <span className="artifact-card-body">
          <strong>{artifact.title}</strong>
          <span>{subtitle}</span>
        </span>
        <span className={`artifact-card-status ${artifact.status}`}>
          {artifact.status === "ready" ? "Ready" : artifact.status === "failed" ? "Failed" : "Streaming"}
        </span>
      </button>
    </>
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

      {step.thinking.length ? <ThinkingBlock chunks={step.thinking} running={step.status === "running"} stepElapsedSeconds={step.elapsedSeconds} /> : null}

      <div className="tool-grid">
        {step.tools.map((tool) => (
          <ToolCard key={tool.id} tool={tool} />
        ))}
      </div>

      {step.text.length ? <AssistantText chunks={step.text} /> : null}
    </section>
  );
}

// Thinking content can get long (multi-paragraph chain-of-thought).
// While the model is still streaming we expand to show progress; once
// it stops we collapse. Even when "expanded", anything past
// THINKING_PREVIEW_LINES is hidden behind a "show all" toggle so the
// chat scroll stays readable.
const THINKING_PREVIEW_LINES = 3;

function ThinkingBlock({ chunks, running, stepElapsedSeconds }: { chunks: string[]; running: boolean; stepElapsedSeconds?: number }) {
  // Default collapsed; auto-expand while streaming so the user sees
  // progress, then collapse once finished.
  const [open, setOpen] = useState(running);
  const [showAll, setShowAll] = useState(false);
  // Wall-clock frontend timer — used only while the step is still running
  // (no backend elapsed yet). Once step_end arrives, stepElapsedSeconds
  // takes over as the authoritative value so the label matches the step header.
  const startedAtRef = useRef<number | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    setOpen(running);
    if (!running) setShowAll(false);
  }, [running]);

  useEffect(() => {
    if (startedAtRef.current === null && chunks.length > 0) {
      startedAtRef.current = performance.now();
    }
  }, [chunks.length]);

  useEffect(() => {
    if (!running || startedAtRef.current === null) return;
    const id = window.setInterval(() => setTick((value) => value + 1), 250);
    return () => window.clearInterval(id);
  }, [running]);
  void tick;

  // After the step ends, use the backend-reported elapsed time; while still
  // running fall back to the frontend wall-clock so the counter ticks live.
  const elapsedSeconds = !running && stepElapsedSeconds != null && stepElapsedSeconds > 0
    ? stepElapsedSeconds
    : startedAtRef.current === null
      ? 0
      : (performance.now() - startedAtRef.current) / 1000;
  const elapsedLabel = elapsedSeconds < 10
    ? `${elapsedSeconds.toFixed(1)}s`
    : `${Math.round(elapsedSeconds)}s`;

  const fullText = chunks.join("");
  const lines = fullText.split(/\r?\n/);
  const truncated = lines.length > THINKING_PREVIEW_LINES;
  const preview = truncated && !showAll
    ? lines.slice(0, THINKING_PREVIEW_LINES).join("\n")
    : fullText;

  return (
    <div className="thinking-block">
      <button type="button" onClick={() => setOpen((value) => !value)}>
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <span>
          {running ? `思考中… ${elapsedLabel}` : `思考 ${elapsedLabel} · ${lines.length} 行`}
        </span>
      </button>
      {open ? (
        <>
          <p>{preview}</p>
          {truncated ? (
            <button
              type="button"
              className="thinking-toggle"
              onClick={() => setShowAll((value) => !value)}
            >
              {showAll ? "收起" : `展开全部 (${lines.length - THINKING_PREVIEW_LINES} 行更多)`}
            </button>
          ) : null}
        </>
      ) : null}
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

const PLACEHOLDER_LABELS: Record<string, { title: string; hint: string }> = {
  experts: { title: "专家", hint: "专家与专家团即将上线，敬请期待。" },
  connectors: { title: "连接器", hint: "外部工具连接器即将上线，敬请期待。" },
  automation: { title: "自动化", hint: "定时任务与自动化即将上线，敬请期待。" },
  more: { title: "更多", hint: "资料库与灵感即将上线，敬请期待。" },
};

function PlaceholderView({
  view,
  sidebarOpen,
  onExpandSidebar,
}: {
  view: AppView;
  sidebarOpen: boolean;
  onExpandSidebar: () => void;
}) {
  const meta = PLACEHOLDER_LABELS[view] ?? { title: "敬请期待", hint: "该功能正在开发中。" };
  return (
    <section className="placeholder-view">
      {!sidebarOpen ? (
        <button className="floating-sidebar-toggle" type="button" onClick={onExpandSidebar} title="展开侧栏">
          <Menu size={19} />
        </button>
      ) : null}
      <div className="placeholder-card">
        <Sparkles size={28} />
        <h1>{meta.title}</h1>
        <p>{meta.hint}</p>
      </div>
    </section>
  );
}

function EmptyChat({
  capabilities,
  composer,
  onPick,
  expertRole,
  teamName,
  teamMembers,
}: {
  capabilities: CapabilitySummary | null;
  composer: ReactNode;
  onPick: (prompt: string) => void;
  expertRole?: string;
  teamName?: string;
  teamMembers?: TeamMember[];
}) {
  const title = teamName ?? expertRole ?? "AgentEngine";
  return (
    <div className="empty-chat">
      <div className="empty-mark">
        <Sparkles size={22} />
      </div>
      <h1>{title}</h1>
      {teamName && teamMembers ? (
        <div className="chat-expert-hint">
          <Sparkles size={14} /> 专家团接力：{teamMembers.map((m) => m.role).join(" → ")}
        </div>
      ) : expertRole ? (
        <div className="chat-expert-hint">
          <Sparkles size={14} /> 正在与「{expertRole}」对话
        </div>
      ) : (
        <CapabilityStats capabilities={capabilities} />
      )}
      <div className="empty-composer">{composer}</div>
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

function CapabilityPanel({
  capabilities,
  skillCount,
  onManageSkills,
}: {
  capabilities: CapabilitySummary | null;
  skillCount: number;
  onManageSkills: () => void;
}) {
  const toolCount = capabilities?.tools.length ?? 0;
  const enabledSkillCount = capabilities?.skills.length ?? 0;
  return (
    <section className="capability-panel">
      <div className="capability-panel-head">
        <Wrench size={15} />
        <span>能力</span>
        <button
          className="capability-manage-button"
          type="button"
          onClick={onManageSkills}
          title="管理技能"
        >
          <Settings2 size={13} />
          <span>技能</span>
        </button>
      </div>
      <p>
        {toolCount} tools / {enabledSkillCount} skills 已启用
        {skillCount > enabledSkillCount ? `（共 ${skillCount}）` : ""}
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

function loadPersistedSessions(): ChatSession[] {
  try {
    const raw = localStorage.getItem(CHAT_SESSIONS_STORAGE_KEY);
    if (!raw) {
      return [createChatSession("welcome", "新会话")];
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [createChatSession("welcome", "新会话")];
    }
    const restored = parsed
      .map((value, index) => normalizePersistedSession(value, index))
      .filter((session): session is ChatSession => Boolean(session));
    return restored.length ? restored : [createChatSession("welcome", "新会话")];
  } catch {
    return [createChatSession("welcome", "新会话")];
  }
}

function loadPersistedActiveSessionId(sessions: ChatSession[]): string {
  try {
    const saved = localStorage.getItem(ACTIVE_SESSION_STORAGE_KEY);
    if (saved && sessions.some((session) => session.id === saved)) {
      return saved;
    }
  } catch {
    // Ignore storage read failures and fall back to the first restored session.
  }
  return sessions[0]?.id ?? "welcome";
}

function persistSessions(sessions: ChatSession[], activeSessionId: string) {
  try {
    localStorage.setItem(
      CHAT_SESSIONS_STORAGE_KEY,
      JSON.stringify(sessions.slice(0, MAX_PERSISTED_SESSIONS).map(prepareSessionForStorage)),
    );
    if (sessions.some((session) => session.id === activeSessionId)) {
      localStorage.setItem(ACTIVE_SESSION_STORAGE_KEY, activeSessionId);
    }
  } catch (error) {
    console.warn("Failed to persist chat sessions", error);
  }
}

function prepareSessionForStorage(session: ChatSession): ChatSession {
  return {
    ...session,
    isRunning: false,
  };
}

function normalizePersistedSession(value: unknown, index: number): ChatSession | null {
  if (!isStorageRecord(value)) {
    return null;
  }
  const turns = Array.isArray(value.turns)
    ? value.turns
        .map((turn) => normalizePersistedTurn(turn))
        .filter((turn): turn is ChatTurn => Boolean(turn))
    : [];
  const agentName = stringStorageValue(value.agentName);
  const expertRole = stringStorageValue(value.expertRole);
  const teamName = stringStorageValue(value.teamName);
  const teamMembers = Array.isArray(value.teamMembers)
    ? (value.teamMembers as TeamMember[]).filter(
        (m) => m && typeof m.name === "string" && typeof m.role === "string",
      )
    : undefined;
  return {
    id: stringStorageValue(value.id) || `session-${index}`,
    title: stringStorageValue(value.title) || "新会话",
    query: stringStorageValue(value.query),
    turns,
    isRunning: false,
    ...(agentName ? { agentName } : {}),
    ...(expertRole ? { expertRole } : {}),
    ...(teamName ? { teamName } : {}),
    ...(teamMembers && teamMembers.length ? { teamMembers } : {}),
  };
}

function normalizePersistedTurn(value: unknown): ChatTurn | null {
  if (!isStorageRecord(value)) {
    return null;
  }
  const trace = isStorageRecord(value.trace)
    ? normalizePersistedTrace(value.trace)
    : createEmptyTrace();
  const memberLabel = stringStorageValue(value.memberLabel);
  return {
    id: stringStorageValue(value.id) || crypto.randomUUID(),
    submittedQuery: stringStorageValue(value.submittedQuery),
    createdAt: numberStorageValue(value.createdAt) || Date.now(),
    files: Array.isArray(value.files) ? (value.files as ReportFileSummary[]) : [],
    trace,
    ...(memberLabel ? { memberLabel } : {}),
  };
}

function normalizePersistedTrace(value: Record<string, unknown>): RunTrace {
  const trace = {
    ...createEmptyTrace(),
    ...(value as Partial<RunTrace>),
  };
  trace.steps = Array.isArray(trace.steps) ? trace.steps : [];
  trace.todos = Array.isArray(trace.todos) ? trace.todos : [];
  trace.pendingQuestions = Array.isArray(trace.pendingQuestions) ? trace.pendingQuestions : [];
  trace.artifacts = Array.isArray(trace.artifacts) ? trace.artifacts : [];
  if (trace.status === "running") {
    trace.status = "failed";
    trace.error = trace.error || "Run interrupted by page refresh.";
  }
  return trace;
}

function isStorageRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringStorageValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberStorageValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function formatMessageTime(value: number): string {
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
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

function withUploadedFileContext(query: string, files: ReportFileSummary[]): string {
  if (!files.length) {
    return query;
  }
  const fileContext = files.map((file, index) => {
    const parsed = file.parsed;
    const firstSheet = parsed.sheets[0];
    const sheetPreview = firstSheet
      ? [
          `sheet=${firstSheet.name}`,
          `headers=${firstSheet.headers.join(", ")}`,
          `rows=${firstSheet.rows.slice(0, 5).map((row) => row.join(" | ")).join("; ")}`,
        ].join("; ")
      : "";
    const textPreview = parsed.text_preview ? `text=${parsed.text_preview.slice(0, 1500)}` : "";
    return [
      `#${index + 1} ${file.filename}`,
      `type=${parsed.extension}`,
      `size=${file.size_bytes}`,
      sheetPreview,
      textPreview,
    ].filter(Boolean).join("\n");
  }).join("\n\n");
  return `${query}\n\nUploaded file context from this conversation:\n${fileContext}`;
}

function fileKindLabel(file: ReportFileSummary): string {
  const parsed = file.parsed;
  if (parsed.sheets.length) {
    return `${parsed.extension.toUpperCase()} / ${parsed.sheets.length} sheet${parsed.sheets.length > 1 ? "s" : ""}`;
  }
  if (parsed.page_count) {
    return `${parsed.extension.toUpperCase()} / ${parsed.page_count} page${parsed.page_count > 1 ? "s" : ""}`;
  }
  if (parsed.text_preview) {
    return `${parsed.extension.toUpperCase()} / text preview`;
  }
  return `${parsed.extension.toUpperCase()} / ${formatBytes(file.size_bytes)}`;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function titleFromQuery(query: string): string {
  const title = query.length > 28 ? `${query.slice(0, 28)}...` : query;
  return title || "新会话";
}

// The answer a member produced: prefer streamed step text (how chat models
// reply here), fall back to the final-result summary.
function extractTraceText(trace: RunTrace): string {
  const stepText = trace.steps
    .map((step) => step.text.join(""))
    .join("\n")
    .trim();
  if (stepText) {
    return stepText;
  }
  return (trace.finalText ?? "").trim();
}
