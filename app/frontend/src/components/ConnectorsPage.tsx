import { useEffect, useState, useCallback } from "react";
import {
  Cable,
  ChevronDown,
  ChevronRight,
  Loader2,
  Plus,
  Trash2,
  ToggleLeft,
  ToggleRight,
  Terminal,
  Globe,
  X,
  Zap,
} from "lucide-react";
import type { McpConnector } from "../types";
import { apiFetch } from "../auth";
import { useI18n } from "../i18n";

const API = "";

type Draft = {
  name: string;
  transport: "stdio" | "sse";
  command: string;
  args: string;
  env: string;
  url: string;
};

const EMPTY_DRAFT: Draft = {
  name: "",
  transport: "stdio",
  command: "",
  args: "",
  env: "",
  url: "",
};

type TestState =
  | { status: "loading" }
  | { status: "ok"; tools: { name: string; description: string }[] }
  | { status: "error"; error: string };

export function ConnectorsPage() {
  const { locale } = useI18n();
  const [connectors, setConnectors] = useState<McpConnector[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [submitting, setSubmitting] = useState(false);
  const [testResults, setTestResults] = useState<Record<string, TestState>>({});
  const [testExpanded, setTestExpanded] = useState<Record<string, boolean>>({});

  const fetchConnectors = useCallback(async () => {
    try {
      const res = await apiFetch(`${API}/api/connectors`);
      if (!res.ok) throw new Error("Failed to load");
      const data = await res.json();
      setConnectors(data.connectors ?? []);
      setError("");
    } catch {
      setError(locale === "zh" ? "加载连接器失败" : "Failed to load connectors");
    } finally {
      setLoading(false);
    }
  }, [locale]);

  useEffect(() => {
    fetchConnectors();
  }, [fetchConnectors]);

  async function handleToggle(id: string) {
    const prev = connectors;
    setConnectors((list) =>
      list.map((c) => (c.id === id ? { ...c, enabled: !c.enabled } : c)),
    );
    try {
      const res = await apiFetch(`${API}/api/connectors/${id}/toggle`, { method: "PATCH" });
      if (!res.ok) throw new Error("failed");
    } catch {
      setConnectors(prev);
      setError(locale === "zh" ? "操作失败" : "Toggle failed");
    }
  }

  async function handleDelete(id: string) {
    const prev = connectors;
    setConnectors((list) => list.filter((c) => c.id !== id));
    try {
      const res = await apiFetch(`${API}/api/connectors/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("failed");
    } catch {
      setConnectors(prev);
      setError(locale === "zh" ? "删除失败" : "Delete failed");
    }
  }

  async function handleAdd() {
    if (!draft.name.trim()) return;
    setSubmitting(true);
    const body: Record<string, unknown> = {
      name: draft.name.trim(),
      transport: draft.transport,
    };
    if (draft.transport === "stdio") {
      body.command = draft.command.trim();
      body.args = draft.args
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      body.env = Object.fromEntries(
        draft.env
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean)
          .filter((s) => s.includes("="))
          .map((line) => {
            const idx = line.indexOf("=");
            return [line.slice(0, idx), line.slice(idx + 1)];
          }),
      );
    } else {
      body.url = draft.url.trim();
    }
    try {
      const res = await apiFetch(`${API}/api/connectors`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error("failed");
      setShowForm(false);
      setDraft(EMPTY_DRAFT);
      await fetchConnectors();
    } catch {
      setError(locale === "zh" ? "添加失败" : "Add failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleTest(id: string) {
    setTestResults((map) => ({ ...map, [id]: { status: "loading" } }));
    try {
      const res = await apiFetch(`${API}/api/connectors/${id}/test`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as {
        ok?: boolean;
        tools?: { name: string; description: string }[];
        error?: string;
      };
      if (data.ok) {
        setTestResults((map) => ({
          ...map,
          [id]: { status: "ok", tools: data.tools ?? [] },
        }));
      } else {
        setTestResults((map) => ({
          ...map,
          [id]: {
            status: "error",
            error: data.error || (locale === "zh" ? "连接失败" : "Connection failed"),
          },
        }));
      }
    } catch (err) {
      setTestResults((map) => ({
        ...map,
        [id]: {
          status: "error",
          error: err instanceof Error ? err.message : String(err),
        },
      }));
    }
  }

  const isZh = locale === "zh";

  return (
    <section className="connectors-page">
      <div className="connectors-page-head">
        <div>
          <h2>{isZh ? "连接器" : "Connectors"}</h2>
          <p className="connectors-subtitle">
            {isZh
              ? "管理 MCP 服务器连接，让 Agent 访问外部工具与数据源"
              : "Manage MCP server connections to give agents access to external tools and data"}
          </p>
        </div>
        <button
          type="button"
          className="connector-add-btn"
          onClick={() => setShowForm(true)}
        >
          <Plus size={17} />
          <span>{isZh ? "添加连接器" : "Add connector"}</span>
        </button>
      </div>

      {error ? <p className="connectors-error">{error}</p> : null}

      {loading ? (
        <p className="connectors-empty">
          {isZh ? "加载中..." : "Loading..."}
        </p>
      ) : connectors.length === 0 && !showForm ? (
        <div className="connectors-empty">
          <Cable size={36} strokeWidth={1.2} />
          <p>{isZh ? "暂无连接器，点击上方按钮添加" : "No connectors yet. Add one to get started."}</p>
        </div>
      ) : (
        <ul className="connector-list">
          {connectors.map((c) => (
            <li key={c.id} className={`connector-card${c.enabled ? "" : " is-disabled"}`}>
              <div className="connector-card-icon">
                {c.transport === "stdio" ? (
                  <Terminal size={20} />
                ) : (
                  <Globe size={20} />
                )}
              </div>
              <div className="connector-card-body">
                <strong className="connector-card-name">{c.name}</strong>
                <span className="connector-card-detail">
                  {c.transport === "stdio"
                    ? `${c.command} ${c.args.join(" ")}`
                    : c.url}
                </span>
                {testResults[c.id] ? (
                  <div className="connector-test-result">
                    {testResults[c.id].status === "loading" ? (
                      <span className="connector-test-loading">
                        <Loader2 size={13} className="connector-test-spin" />
                        {isZh ? "测试中..." : "Testing..."}
                      </span>
                    ) : testResults[c.id].status === "ok" ? (
                      <>
                        <button
                          type="button"
                          className="connector-test-summary is-ok"
                          onClick={() =>
                            setTestExpanded((map) => ({ ...map, [c.id]: !map[c.id] }))
                          }
                        >
                          {testExpanded[c.id] ? (
                            <ChevronDown size={13} />
                          ) : (
                            <ChevronRight size={13} />
                          )}
                          {isZh
                            ? `发现 ${(testResults[c.id] as { tools: unknown[] }).tools.length} 个工具`
                            : `Found ${(testResults[c.id] as { tools: unknown[] }).tools.length} tools`}
                        </button>
                        {testExpanded[c.id] ? (
                          <ul className="connector-test-tools">
                            {(testResults[c.id] as { tools: { name: string; description: string }[] }).tools.map(
                              (tool) => (
                                <li key={tool.name} title={tool.description}>
                                  {tool.name}
                                </li>
                              ),
                            )}
                          </ul>
                        ) : null}
                      </>
                    ) : (
                      <span className="connector-test-error">
                        {(testResults[c.id] as { error: string }).error}
                      </span>
                    )}
                  </div>
                ) : null}
              </div>
              <div className="connector-card-actions">
                <button
                  type="button"
                  className="connector-action-btn"
                  onClick={() => handleTest(c.id)}
                  disabled={testResults[c.id]?.status === "loading"}
                  title={isZh ? "测试连接" : "Test connection"}
                >
                  <Zap size={16} />
                </button>
                <button
                  type="button"
                  className="connector-action-btn"
                  onClick={() => handleToggle(c.id)}
                  title={c.enabled ? (isZh ? "禁用" : "Disable") : (isZh ? "启用" : "Enable")}
                >
                  {c.enabled ? (
                    <ToggleRight size={22} className="toggle-on" />
                  ) : (
                    <ToggleLeft size={22} className="toggle-off" />
                  )}
                </button>
                <button
                  type="button"
                  className="connector-action-btn connector-delete-btn"
                  onClick={() => handleDelete(c.id)}
                  title={isZh ? "删除" : "Delete"}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {showForm ? (
        <div className="connector-form-overlay" onClick={() => setShowForm(false)}>
          <div
            className="connector-form"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label={isZh ? "添加连接器" : "Add connector"}
          >
            <div className="connector-form-head">
              <h3>{isZh ? "添加连接器" : "Add connector"}</h3>
              <button
                type="button"
                className="connector-form-close"
                onClick={() => setShowForm(false)}
              >
                <X size={18} />
              </button>
            </div>

            <label className="connector-field">
              <span>{isZh ? "名称" : "Name"}</span>
              <input
                type="text"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder={isZh ? "例如 GitHub" : "e.g. GitHub"}
              />
            </label>

            <label className="connector-field">
              <span>{isZh ? "传输方式" : "Transport"}</span>
              <select
                value={draft.transport}
                onChange={(e) =>
                  setDraft({ ...draft, transport: e.target.value as "stdio" | "sse" })
                }
              >
                <option value="stdio">STDIO (CLI)</option>
                <option value="sse">SSE (HTTP)</option>
              </select>
            </label>

            {draft.transport === "stdio" ? (
              <>
                <label className="connector-field">
                  <span>{isZh ? "命令" : "Command"}</span>
                  <input
                    type="text"
                    value={draft.command}
                    onChange={(e) => setDraft({ ...draft, command: e.target.value })}
                    placeholder="uvx"
                  />
                </label>
                <label className="connector-field">
                  <span>{isZh ? "参数（每行一个）" : "Args (one per line)"}</span>
                  <textarea
                    rows={2}
                    value={draft.args}
                    onChange={(e) => setDraft({ ...draft, args: e.target.value })}
                    placeholder="mcp-server-github"
                  />
                </label>
                <label className="connector-field">
                  <span>{isZh ? "环境变量（KEY=VALUE，每行一个）" : "Env (KEY=VALUE, one per line)"}</span>
                  <textarea
                    rows={2}
                    value={draft.env}
                    onChange={(e) => setDraft({ ...draft, env: e.target.value })}
                    placeholder="GITHUB_TOKEN=ghp_xxx"
                  />
                </label>
              </>
            ) : (
              <label className="connector-field">
                <span>{isZh ? "SSE URL" : "SSE URL"}</span>
                <input
                  type="text"
                  value={draft.url}
                  onChange={(e) => setDraft({ ...draft, url: e.target.value })}
                  placeholder="https://mcp.example.com/sse"
                />
              </label>
            )}

            <div className="connector-form-actions">
              <button
                type="button"
                className="connector-form-cancel"
                onClick={() => setShowForm(false)}
              >
                {isZh ? "取消" : "Cancel"}
              </button>
              <button
                type="button"
                className="connector-form-submit"
                onClick={handleAdd}
                disabled={!draft.name.trim() || submitting}
              >
                {submitting
                  ? (isZh ? "添加中..." : "Adding...")
                  : (isZh ? "添加" : "Add")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
