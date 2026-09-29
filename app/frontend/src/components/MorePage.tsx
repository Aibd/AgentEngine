import { useEffect, useState } from "react";
import {
  Archive,
  FileDown,
  FileText,
  FolderOpen,
  Lightbulb,
  Menu,
} from "lucide-react";
import type { ScenarioGroup } from "../types";
import { apiFetch } from "../auth";
import { fetchExpertScenarios } from "../traceTransport";
import { useI18n } from "../i18n";

const API = "";
const CHAT_SESSIONS_STORAGE_KEY = "agentengine.web.sessions.v1";

type MorePageProps = {
  onUsePrompt: (prompt: string) => void;
  sidebarOpen: boolean;
  onExpandSidebar: () => void;
};

type LibraryItem =
  | {
      kind: "report";
      id: string;
      title: string;
      status: string;
      createdAt: string;
    }
  | {
      kind: "file";
      id: string;
      title: string;
      createdAt: string;
    };

type PromptGroup = {
  groupZh: string;
  groupEn: string;
  prompts: { zh: string; en: string }[];
};

const CURATED_PROMPTS: PromptGroup[] = [
  {
    groupZh: "写作",
    groupEn: "Writing",
    prompts: [
      {
        zh: "帮我写一篇面向技术团队的博客文章，主题是「如何在项目中引入 AI Agent」，要求结构清晰、有实际案例。",
        en: "Write a blog post for a technical audience about introducing AI agents into a project, with clear structure and real examples.",
      },
      {
        zh: "把这份会议纪要改写成一封简洁的跟进邮件，列出行动项、负责人和截止时间。",
        en: "Turn these meeting notes into a concise follow-up email with action items, owners, and deadlines.",
      },
      {
        zh: "为我的产品写三个不同风格的发布说明草稿：正式版、轻松版、极客版。",
        en: "Draft three release-note variants for my product: formal, casual, and geeky.",
      },
    ],
  },
  {
    groupZh: "研究",
    groupEn: "Research",
    prompts: [
      {
        zh: "调研 2025 年向量数据库的主流选型，从性能、成本、生态三个维度对比，并给出推荐。",
        en: "Research mainstream vector database options in 2025; compare performance, cost, and ecosystem, then give a recommendation.",
      },
      {
        zh: "帮我整理这个话题的已有研究脉络：关键论文、主要观点和尚未解决的问题。",
        en: "Map the research landscape of this topic: key papers, main viewpoints, and open problems.",
      },
      {
        zh: "对比三家竞争对手的定价策略，总结各自的目标客户和差异化卖点。",
        en: "Compare the pricing strategies of three competitors; summarize target customers and differentiators.",
      },
    ],
  },
  {
    groupZh: "数据分析",
    groupEn: "Data analysis",
    prompts: [
      {
        zh: "分析这份 CSV 里的销售数据：找出月度趋势、异常值，并给出下季度的预测。",
        en: "Analyze the sales data in this CSV: find monthly trends, outliers, and forecast next quarter.",
      },
      {
        zh: "帮我把这份问卷结果做成一份可视化报告，包含关键图表和结论摘要。",
        en: "Turn this survey result into a visual report with key charts and an executive summary.",
      },
    ],
  },
  {
    groupZh: "代码",
    groupEn: "Code",
    prompts: [
      {
        zh: "审查这个仓库的代码，找出潜在的性能瓶颈和安全风险，按严重程度排序。",
        en: "Review this repository for performance bottlenecks and security risks, ordered by severity.",
      },
      {
        zh: "为这个模块设计一套单元测试，覆盖正常路径、边界条件和错误处理。",
        en: "Design a unit test suite for this module covering happy paths, edge cases, and error handling.",
      },
    ],
  },
];

function readSessionIds(): string[] {
  try {
    const raw = localStorage.getItem(CHAT_SESSIONS_STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const ids = parsed
      .map((item) =>
        item && typeof item === "object" && typeof (item as { id?: unknown }).id === "string"
          ? (item as { id: string }).id
          : "",
      )
      .filter(Boolean);
    return Array.from(new Set(ids));
  } catch {
    return [];
  }
}

async function downloadWithAuth(url: string, filename: string): Promise<void> {
  const res = await apiFetch(url);
  if (!res.ok) throw new Error(`download failed: ${res.status}`);
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}

export function MorePage({ onUsePrompt, sidebarOpen, onExpandSidebar }: MorePageProps) {
  const { locale } = useI18n();
  const isZh = locale === "zh";
  const [tab, setTab] = useState<"library" | "inspiration">("library");
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [libraryLoading, setLibraryLoading] = useState(true);
  const [libraryError, setLibraryError] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const [scenarios, setScenarios] = useState<ScenarioGroup[]>([]);

  useEffect(() => {
    let cancelled = false;

    async function loadLibrary() {
      const sessionIds = readSessionIds();
      try {
        const collected: LibraryItem[] = [];
        await Promise.all(
          sessionIds.map(async (conversationId) => {
            const params = new URLSearchParams({ conversation_id: conversationId });
            const [reportsRes, filesRes] = await Promise.all([
              apiFetch(`${API}/api/reports?${params.toString()}`),
              apiFetch(`${API}/api/report-files?${params.toString()}`),
            ]);
            if (reportsRes.ok) {
              const body = (await reportsRes.json()) as {
                reports?: { report_id?: string; title?: string; status?: string; created_at?: string }[];
              };
              for (const report of body.reports ?? []) {
                if (!report.report_id) continue;
                collected.push({
                  kind: "report",
                  id: report.report_id,
                  title: report.title || (isZh ? "未命名报告" : "Untitled report"),
                  status: report.status || "",
                  createdAt: report.created_at || "",
                });
              }
            }
            if (filesRes.ok) {
              const body = (await filesRes.json()) as {
                files?: { file_id?: string; filename?: string; created_at?: string }[];
              };
              for (const file of body.files ?? []) {
                if (!file.file_id) continue;
                collected.push({
                  kind: "file",
                  id: file.file_id,
                  title: file.filename || (isZh ? "未命名文件" : "Untitled file"),
                  createdAt: file.created_at || "",
                });
              }
            }
          }),
        );
        collected.sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
        if (!cancelled) {
          setItems(collected);
          setLibraryError("");
        }
      } catch {
        if (!cancelled) {
          setLibraryError(isZh ? "加载资料库失败" : "Failed to load library");
        }
      } finally {
        if (!cancelled) setLibraryLoading(false);
      }
    }

    void loadLibrary();
    return () => {
      cancelled = true;
    };
  }, [isZh]);

  useEffect(() => {
    let cancelled = false;
    fetchExpertScenarios()
      .then((groups) => {
        if (!cancelled) setScenarios(groups);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleDownload(url: string, filename: string) {
    setDownloadError("");
    try {
      await downloadWithAuth(url, filename);
    } catch {
      setDownloadError(isZh ? "下载失败" : "Download failed");
    }
  }

  return (
    <section className="more-page">
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

      <div className="more-page-head">
        <div>
          <h2>{isZh ? "更多" : "More"}</h2>
          <p className="more-subtitle">
            {isZh
              ? "浏览你的报告与文件，或从灵感模板快速开始"
              : "Browse your reports and files, or start fast from curated prompts"}
          </p>
        </div>
        <div className="more-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "library"}
            className={`more-tab${tab === "library" ? " is-active" : ""}`}
            onClick={() => setTab("library")}
          >
            <Archive size={15} />
            <span>{isZh ? "资料库" : "Library"}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "inspiration"}
            className={`more-tab${tab === "inspiration" ? " is-active" : ""}`}
            onClick={() => setTab("inspiration")}
          >
            <Lightbulb size={15} />
            <span>{isZh ? "灵感" : "Inspiration"}</span>
          </button>
        </div>
      </div>

      {tab === "library" ? (
        <>
          {libraryError ? <p className="more-error">{libraryError}</p> : null}
          {downloadError ? <p className="more-error">{downloadError}</p> : null}
          {libraryLoading ? (
            <p className="more-empty">{isZh ? "加载中..." : "Loading..."}</p>
          ) : items.length === 0 ? (
            <div className="more-empty">
              <FolderOpen size={36} strokeWidth={1.2} />
              <p>
                {isZh
                  ? "资料库还是空的，去聊天里生成报告或上传文件吧"
                  : "Your library is empty. Generate a report or upload files in chat."}
              </p>
            </div>
          ) : (
            <ul className="more-library-list">
              {items.map((item) => (
                <li key={`${item.kind}-${item.id}`} className="more-library-card">
                  <div className="more-library-icon">
                    {item.kind === "report" ? <FileText size={18} /> : <FileDown size={18} />}
                  </div>
                  <div className="more-library-body">
                    <strong className="more-library-title">{item.title}</strong>
                    <span className="more-library-meta">
                      {item.kind === "report"
                        ? `${isZh ? "报告" : "Report"}${item.status ? ` · ${item.status}` : ""}`
                        : isZh ? "文件" : "File"}
                      {item.createdAt
                        ? ` · ${new Date(item.createdAt).toLocaleString()}`
                        : ""}
                    </span>
                  </div>
                  <div className="more-library-actions">
                    {item.kind === "report" ? (
                      <>
                        <button
                          type="button"
                          className="more-library-btn"
                          onClick={() =>
                            void handleDownload(
                              `${API}/api/reports/${encodeURIComponent(item.id)}/exports/pdf`,
                              `${item.title}.pdf`,
                            )
                          }
                        >
                          {isZh ? "导出 PDF" : "Export PDF"}
                        </button>
                        <button
                          type="button"
                          className="more-library-btn"
                          onClick={() =>
                            void handleDownload(
                              `${API}/api/reports/${encodeURIComponent(item.id)}/exports/docx`,
                              `${item.title}.docx`,
                            )
                          }
                        >
                          {isZh ? "导出 DOCX" : "Export DOCX"}
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className="more-library-btn"
                        onClick={() =>
                          void handleDownload(
                            `${API}/api/report-files/${encodeURIComponent(item.id)}`,
                            item.title,
                          )
                        }
                      >
                        {isZh ? "下载" : "Download"}
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <div className="more-inspiration">
          {CURATED_PROMPTS.map((group) => (
            <section key={group.groupEn} className="more-prompt-group">
              <h3 className="more-prompt-group-title">
                {isZh ? group.groupZh : group.groupEn}
              </h3>
              <div className="more-prompt-grid">
                {group.prompts.map((prompt) => (
                  <button
                    key={prompt.en}
                    type="button"
                    className="more-prompt-card"
                    onClick={() => onUsePrompt(isZh ? prompt.zh : prompt.en)}
                  >
                    {isZh ? prompt.zh : prompt.en}
                  </button>
                ))}
              </div>
            </section>
          ))}
          {scenarios.map((group) => (
            <section key={group.scenario} className="more-prompt-group">
              <h3 className="more-prompt-group-title">{group.scenario}</h3>
              <div className="more-prompt-grid">
                {group.experts.map((expert) => {
                  const prompt = isZh
                    ? `请作为${expert.role || expert.name}协助我：${expert.description}`
                    : `Act as ${expert.role || expert.name}: ${expert.description}`;
                  return (
                    <button
                      key={expert.name}
                      type="button"
                      className="more-prompt-card"
                      onClick={() => onUsePrompt(prompt)}
                    >
                      <span className="more-prompt-card-role">
                        {expert.role || expert.name}
                      </span>
                      {expert.description}
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </section>
  );
}
