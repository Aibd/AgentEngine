import { useEffect, useState, useCallback } from "react";
import {
  CalendarClock,
  ChevronDown,
  ChevronRight,
  Menu,
  Play,
  Plus,
  Trash2,
  ToggleLeft,
  ToggleRight,
  X,
} from "lucide-react";
import type { Automation, AutomationRun, Expert } from "../types";
import { apiFetch } from "../auth";
import { useI18n } from "../i18n";

const API = "";

type CronPreset = "hourly" | "daily" | "weekly" | "custom";

const PRESET_CRONS: Record<Exclude<CronPreset, "custom">, string> = {
  hourly: "7 * * * *",
  daily: "3 9 * * *",
  weekly: "3 9 * * 1",
};

type Draft = {
  name: string;
  prompt: string;
  agentName: string;
  preset: CronPreset;
  cronCustom: string;
};

const EMPTY_DRAFT: Draft = {
  name: "",
  prompt: "",
  agentName: "general_chat",
  preset: "daily",
  cronCustom: "",
};

type RunsState = {
  loading: boolean;
  runs: AutomationRun[];
  error: string;
};

type AutomationPageProps = {
  sidebarOpen: boolean;
  onExpandSidebar: () => void;
};

export function AutomationPage({ sidebarOpen, onExpandSidebar }: AutomationPageProps) {
  const { locale } = useI18n();
  const isZh = locale === "zh";
  const [automations, setAutomations] = useState<Automation[]>([]);
  const [experts, setExperts] = useState<Expert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [submitting, setSubmitting] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [runsById, setRunsById] = useState<Record<string, RunsState>>({});
  const [runningNow, setRunningNow] = useState<Record<string, boolean>>({});

  const fetchAutomations = useCallback(async () => {
    try {
      const res = await apiFetch(`${API}/api/automations`);
      if (!res.ok) throw new Error("Failed to load");
      const data = (await res.json()) as { automations?: Automation[] };
      setAutomations(data.automations ?? []);
      setError("");
    } catch {
      setError(isZh ? "加载自动化任务失败" : "Failed to load automations");
    } finally {
      setLoading(false);
    }
  }, [isZh]);

  useEffect(() => {
    fetchAutomations();
  }, [fetchAutomations]);

  useEffect(() => {
    let cancelled = false;
    apiFetch(`${API}/api/experts`)
      .then((res) => (res.ok ? res.json() : { experts: [] }))
      .then((data: { experts?: Expert[] }) => {
        if (!cancelled) setExperts(data.experts ?? []);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleToggle(automation: Automation) {
    const prev = automations;
    setAutomations((list) =>
      list.map((a) => (a.id === automation.id ? { ...a, enabled: !a.enabled } : a)),
    );
    try {
      const res = await apiFetch(`${API}/api/automations/${automation.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !automation.enabled }),
      });
      if (!res.ok) throw new Error("failed");
    } catch {
      setAutomations(prev);
      setError(isZh ? "操作失败" : "Toggle failed");
    }
  }

  async function handleDelete(id: string) {
    const prev = automations;
    setAutomations((list) => list.filter((a) => a.id !== id));
    try {
      const res = await apiFetch(`${API}/api/automations/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("failed");
    } catch {
      setAutomations(prev);
      setError(isZh ? "删除失败" : "Delete failed");
    }
  }

  async function handleRunNow(id: string) {
    setRunningNow((map) => ({ ...map, [id]: true }));
    try {
      const res = await apiFetch(`${API}/api/automations/${id}/run`, { method: "POST" });
      if (!res.ok) throw new Error("failed");
      // The run executes in the background; give it a moment before refreshing.
      window.setTimeout(() => {
        void fetchAutomations();
        if (expanded[id]) void fetchRuns(id);
      }, 2500);
    } catch {
      setError(isZh ? "触发运行失败" : "Failed to trigger run");
    } finally {
      setRunningNow((map) => ({ ...map, [id]: false }));
    }
  }

  async function fetchRuns(id: string) {
    setRunsById((map) => ({
      ...map,
      [id]: { loading: true, runs: map[id]?.runs ?? [], error: "" },
    }));
    try {
      const res = await apiFetch(`${API}/api/automations/${id}/runs`);
      if (!res.ok) throw new Error("failed");
      const data = (await res.json()) as { runs?: AutomationRun[] };
      setRunsById((map) => ({
        ...map,
        [id]: { loading: false, runs: data.runs ?? [], error: "" },
      }));
    } catch {
      setRunsById((map) => ({
        ...map,
        [id]: {
          loading: false,
          runs: [],
          error: isZh ? "加载运行历史失败" : "Failed to load run history",
        },
      }));
    }
  }

  function handleExpand(id: string) {
    const next = !expanded[id];
    setExpanded((map) => ({ ...map, [id]: next }));
    if (next) void fetchRuns(id);
  }

  async function handleAdd() {
    const name = draft.name.trim();
    const prompt = draft.prompt.trim();
    const cron = draft.preset === "custom" ? draft.cronCustom.trim() : PRESET_CRONS[draft.preset];
    if (!name || !prompt || !cron) return;
    setSubmitting(true);
    try {
      const res = await apiFetch(`${API}/api/automations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          prompt,
          agent_name: draft.agentName,
          cron,
          enabled: true,
        }),
      });
      if (!res.ok) {
        if (res.status === 400) {
          throw new Error(isZh ? "cron 表达式无效" : "Invalid cron expression");
        }
        throw new Error("failed");
      }
      setShowForm(false);
      setDraft(EMPTY_DRAFT);
      await fetchAutomations();
    } catch (err) {
      setError(
        err instanceof Error && err.message !== "failed"
          ? err.message
          : isZh
            ? "创建失败"
            : "Create failed",
      );
    } finally {
      setSubmitting(false);
    }
  }

  function statusBadge(a: Automation) {
    if (!a.last_status) {
      return (
        <span className="automation-badge automation-badge-never">
          {isZh ? "从未运行" : "Never run"}
        </span>
      );
    }
    const labels: Record<string, string> = isZh
      ? { success: "成功", failed: "失败", running: "运行中" }
      : { success: "Success", failed: "Failed", running: "Running" };
    return (
      <span className={`automation-badge automation-badge-${a.last_status}`}>
        {labels[a.last_status] ?? a.last_status}
      </span>
    );
  }

  return (
    <section className="automation-page">
      {!sidebarOpen ? (
        <button
          className="floating-sidebar-toggle"
          type="button"
          onClick={onExpandSidebar}
          title={isZh ? "展开侧栏" : "Expand sidebar"}
        >
          <Menu size={19} />
        </button>
      ) : null}

      <div className="automation-page-head">
        <div>
          <h2>{isZh ? "自动化" : "Automation"}</h2>
          <p className="automation-subtitle">
            {isZh
              ? "按 cron 计划定时运行 Agent 任务"
              : "Run agent tasks on a cron schedule"}
          </p>
        </div>
        <button
          type="button"
          className="automation-add-btn"
          onClick={() => setShowForm(true)}
        >
          <Plus size={17} />
          <span>{isZh ? "新建任务" : "New task"}</span>
        </button>
      </div>

      {error ? <p className="automation-error">{error}</p> : null}

      {loading ? (
        <p className="automation-empty">{isZh ? "加载中..." : "Loading..."}</p>
      ) : automations.length === 0 && !showForm ? (
        <div className="automation-empty">
          <CalendarClock size={36} strokeWidth={1.2} />
          <p>
            {isZh
              ? "暂无自动化任务，点击上方按钮创建"
              : "No automations yet. Create one to get started."}
          </p>
        </div>
      ) : (
        <ul className="automation-list">
          {automations.map((a) => {
            const isExpanded = Boolean(expanded[a.id]);
            const runsState = runsById[a.id];
            return (
              <li
                key={a.id}
                className={`automation-card${a.enabled ? "" : " is-disabled"}`}
              >
                <div className="automation-card-main">
                  <div className="automation-card-icon">
                    <CalendarClock size={20} />
                  </div>
                  <div className="automation-card-body">
                    <div className="automation-card-title-row">
                      <strong className="automation-card-name">{a.name}</strong>
                      {statusBadge(a)}
                    </div>
                    <span className="automation-card-detail">
                      {a.agent_name} · {a.cron}
                      {a.next_run_at
                        ? ` · ${isZh ? "下次运行" : "Next"}: ${new Date(a.next_run_at).toLocaleString()}`
                        : ""}
                    </span>
                    {a.last_status === "failed" && a.last_error ? (
                      <span className="automation-card-last-error">{a.last_error}</span>
                    ) : null}
                  </div>
                  <div className="automation-card-actions">
                    <button
                      type="button"
                      className="automation-action-btn"
                      onClick={() => handleRunNow(a.id)}
                      disabled={Boolean(runningNow[a.id])}
                      title={isZh ? "立即运行" : "Run now"}
                    >
                      <Play size={16} className={runningNow[a.id] ? "automation-spin" : undefined} />
                    </button>
                    <button
                      type="button"
                      className="automation-action-btn"
                      onClick={() => handleToggle(a)}
                      title={a.enabled ? (isZh ? "禁用" : "Disable") : (isZh ? "启用" : "Enable")}
                    >
                      {a.enabled ? (
                        <ToggleRight size={22} className="toggle-on" />
                      ) : (
                        <ToggleLeft size={22} className="toggle-off" />
                      )}
                    </button>
                    <button
                      type="button"
                      className="automation-action-btn automation-delete-btn"
                      onClick={() => handleDelete(a.id)}
                      title={isZh ? "删除" : "Delete"}
                    >
                      <Trash2 size={16} />
                    </button>
                    <button
                      type="button"
                      className="automation-action-btn"
                      onClick={() => handleExpand(a.id)}
                      title={isZh ? "运行历史" : "Run history"}
                    >
                      {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    </button>
                  </div>
                </div>
                {isExpanded ? (
                  <div className="automation-runs">
                    {!runsState || runsState.loading ? (
                      <p className="automation-runs-empty">
                        {isZh ? "加载中..." : "Loading..."}
                      </p>
                    ) : runsState.error ? (
                      <p className="automation-runs-empty">{runsState.error}</p>
                    ) : runsState.runs.length === 0 ? (
                      <p className="automation-runs-empty">
                        {isZh ? "暂无运行记录" : "No runs yet"}
                      </p>
                    ) : (
                      <ul className="automation-runs-list">
                        {runsState.runs.map((run, idx) => (
                          <li key={`${run.started_at}-${idx}`} className="automation-run-row">
                            <span className="automation-run-time">
                              {new Date(run.started_at).toLocaleString()}
                            </span>
                            <span className={`automation-badge automation-badge-${run.status}`}>
                              {run.status === "success"
                                ? isZh ? "成功" : "Success"
                                : run.status === "failed"
                                  ? isZh ? "失败" : "Failed"
                                  : isZh ? "运行中" : "Running"}
                            </span>
                            <span className="automation-run-text">
                              {run.status === "failed" ? run.error : run.summary}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      {showForm ? (
        <div className="automation-form-overlay" onClick={() => setShowForm(false)}>
          <div
            className="automation-form"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label={isZh ? "新建任务" : "New task"}
          >
            <div className="automation-form-head">
              <h3>{isZh ? "新建自动化任务" : "New automation"}</h3>
              <button
                type="button"
                className="automation-form-close"
                onClick={() => setShowForm(false)}
              >
                <X size={18} />
              </button>
            </div>

            <label className="automation-field">
              <span>{isZh ? "名称" : "Name"}</span>
              <input
                type="text"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder={isZh ? "例如 每日晨报" : "e.g. Daily briefing"}
              />
            </label>

            <label className="automation-field">
              <span>{isZh ? "任务内容 (Prompt)" : "Prompt"}</span>
              <textarea
                rows={4}
                value={draft.prompt}
                onChange={(e) => setDraft({ ...draft, prompt: e.target.value })}
                placeholder={
                  isZh
                    ? "描述每次运行时 Agent 要完成的任务"
                    : "Describe what the agent should do on each run"
                }
              />
            </label>

            <label className="automation-field">
              <span>{isZh ? "Agent" : "Agent"}</span>
              <select
                value={draft.agentName}
                onChange={(e) => setDraft({ ...draft, agentName: e.target.value })}
              >
                <option value="general_chat">general_chat</option>
                <option value="deep_research">deep_research</option>
                {experts.map((expert) => (
                  <option key={expert.name} value={expert.name}>
                    {expert.name}
                    {expert.role ? ` · ${expert.role}` : ""}
                  </option>
                ))}
              </select>
            </label>

            <label className="automation-field">
              <span>{isZh ? "频率" : "Schedule"}</span>
              <select
                value={draft.preset}
                onChange={(e) => setDraft({ ...draft, preset: e.target.value as CronPreset })}
              >
                <option value="hourly">{isZh ? "每小时" : "Hourly"}</option>
                <option value="daily">{isZh ? "每天 9 点" : "Daily at 9:00"}</option>
                <option value="weekly">{isZh ? "每周一 9 点" : "Mondays at 9:00"}</option>
                <option value="custom">{isZh ? "自定义" : "Custom"}</option>
              </select>
            </label>

            {draft.preset === "custom" ? (
              <label className="automation-field">
                <span>cron</span>
                <input
                  type="text"
                  value={draft.cronCustom}
                  onChange={(e) => setDraft({ ...draft, cronCustom: e.target.value })}
                  placeholder="3 9 * * *"
                />
              </label>
            ) : null}

            <div className="automation-form-actions">
              <button
                type="button"
                className="automation-form-cancel"
                onClick={() => setShowForm(false)}
              >
                {isZh ? "取消" : "Cancel"}
              </button>
              <button
                type="button"
                className="automation-form-submit"
                onClick={handleAdd}
                disabled={
                  !draft.name.trim()
                  || !draft.prompt.trim()
                  || (draft.preset === "custom" && !draft.cronCustom.trim())
                  || submitting
                }
              >
                {submitting
                  ? isZh ? "创建中..." : "Creating..."
                  : isZh ? "创建" : "Create"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
