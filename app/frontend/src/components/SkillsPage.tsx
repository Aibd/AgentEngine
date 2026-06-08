import { useEffect, useMemo, useState } from "react";
import {
  Download,
  Loader2,
  Package,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Star,
  Trash2,
  Check,
  Menu,
} from "lucide-react";
import {
  deleteSkill,
  fetchMarketCategories,
  fetchMarketSkills,
  installMarketSkill,
  setSkillEnabled,
  skillExportUrl,
} from "../traceTransport";
import type { MarketSkill, SkillSummary } from "../types";
import type { AppView } from "./AppNav";

type SkillsPageProps = {
  skills: SkillSummary[];
  onNavigate: (view: AppView) => void;
  onSkillsChanged: () => void;
  onOpenImport: () => void;
  sidebarOpen: boolean;
  onExpandSidebar: () => void;
};

type SubTab = "market" | "installed";

export function SkillsPage({
  skills,
  onNavigate,
  onSkillsChanged,
  onOpenImport,
  sidebarOpen,
  onExpandSidebar,
}: SkillsPageProps) {
  const [subTab, setSubTab] = useState<SubTab>("market");
  const [search, setSearch] = useState("");
  const installedCount = skills.length;

  return (
    <section className="skills-page">
      <header className="skills-page-head">
        {!sidebarOpen ? (
          <button className="skills-page-collapse" type="button" onClick={onExpandSidebar} title="展开侧栏">
            <Menu size={18} />
          </button>
        ) : null}
        <div className="skills-page-tabs" role="tablist">
          <button type="button" className="page-tab" onClick={() => onNavigate("experts")}>
            <Sparkles size={15} /> 专家
          </button>
          <button type="button" className="page-tab is-active" aria-current="page">
            <Package size={15} /> 技能
          </button>
          <button type="button" className="page-tab" onClick={() => onNavigate("connectors")}>
            连接器
          </button>
        </div>
        <div className="skills-page-head-actions">
          <label className="skills-page-search">
            <Search size={15} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索技能"
            />
          </label>
          <button type="button" className="skills-page-add" onClick={onOpenImport}>
            <Plus size={16} /> 添加技能
          </button>
        </div>
      </header>

      <div className="skills-subtabs">
        <button
          type="button"
          className={subTab === "market" ? "subtab is-active" : "subtab"}
          onClick={() => setSubTab("market")}
        >
          技能市场
        </button>
        <button
          type="button"
          className={subTab === "installed" ? "subtab is-active" : "subtab"}
          onClick={() => setSubTab("installed")}
        >
          已安装 ({installedCount})
        </button>
      </div>

      {subTab === "market" ? (
        <MarketTab search={search} onInstalled={onSkillsChanged} />
      ) : (
        <InstalledTab skills={skills} search={search} onChanged={onSkillsChanged} />
      )}
    </section>
  );
}

function MarketTab({ search, onInstalled }: { search: string; onInstalled: () => void }) {
  const [categories, setCategories] = useState<string[]>(["全部"]);
  const [activeCategory, setActiveCategory] = useState("全部");
  const [items, setItems] = useState<MarketSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [installing, setInstalling] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchMarketCategories()
      .then(setCategories)
      .catch(() => setCategories(["全部"]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchMarketSkills(activeCategory, search)
      .then((list) => {
        if (!cancelled) {
          setItems(list);
          setError("");
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [activeCategory, search]);

  async function handleInstall(entry: MarketSkill) {
    setInstalling(entry.id);
    setError("");
    try {
      await installMarketSkill(entry.id);
      setItems((current) =>
        current.map((item) => (item.id === entry.id ? { ...item, installed: true } : item)),
      );
      onInstalled();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling(null);
    }
  }

  return (
    <div className="skill-market">
      <div className="skill-market-toolbar">
        <div className="skill-category-row">
          {categories.map((category) => (
            <button
              key={category}
              type="button"
              className={category === activeCategory ? "category-chip is-active" : "category-chip"}
              onClick={() => setActiveCategory(category)}
            >
              {category}
            </button>
          ))}
        </div>
        <span className="skill-market-source">skillhub.cn</span>
      </div>

      {error ? <p className="skills-feedback is-error">{error}</p> : null}

      {loading ? (
        <div className="skill-market-loading">
          <Loader2 className="spin" size={20} /> 加载中…
        </div>
      ) : items.length === 0 ? (
        <div className="skill-market-empty">
          <Package size={26} />
          <span>没有匹配的技能。</span>
        </div>
      ) : (
        <div className="skill-market-grid">
          {items.map((entry) => (
            <MarketCard
              key={entry.id}
              entry={entry}
              installing={installing === entry.id}
              onInstall={() => handleInstall(entry)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function MarketCard({
  entry,
  installing,
  onInstall,
}: {
  entry: MarketSkill;
  installing: boolean;
  onInstall: () => void;
}) {
  return (
    <article className="market-card">
      <div className="market-card-icon">{entry.icon || entry.name.slice(0, 1).toUpperCase()}</div>
      <div className="market-card-body">
        <strong className="market-card-name">{entry.name}</strong>
        <p className="market-card-desc">{entry.description}</p>
        <div className="market-card-meta">
          <span title="下载量">
            <Download size={12} /> {formatCount(entry.downloads)}
          </span>
          <span title="评分">
            <Star size={12} /> {entry.rating.toFixed(1)}
          </span>
        </div>
      </div>
      {entry.installed ? (
        <span className="market-card-installed" title="已安装">
          <Check size={16} />
        </span>
      ) : (
        <button
          type="button"
          className="market-card-install"
          onClick={onInstall}
          disabled={installing}
          title="安装"
          aria-label={`安装 ${entry.name}`}
        >
          {installing ? <Loader2 className="spin" size={16} /> : <Plus size={16} />}
        </button>
      )}
    </article>
  );
}

function InstalledTab({
  skills,
  search,
  onChanged,
}: {
  skills: SkillSummary[];
  search: string;
  onChanged: () => void;
}) {
  const [busyName, setBusyName] = useState<string | null>(null);
  const [error, setError] = useState("");

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const sorted = [...skills].sort((a, b) => a.name.localeCompare(b.name));
    if (!q) {
      return sorted;
    }
    return sorted.filter(
      (skill) =>
        skill.name.toLowerCase().includes(q) || skill.description.toLowerCase().includes(q),
    );
  }, [skills, search]);

  async function toggle(skill: SkillSummary) {
    setBusyName(skill.name);
    setError("");
    try {
      await setSkillEnabled(skill.name, !skill.enabled);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyName(null);
    }
  }

  async function remove(skill: SkillSummary) {
    if (!window.confirm(`确定删除技能「${skill.name}」？该操作会从磁盘移除其文件。`)) {
      return;
    }
    setBusyName(skill.name);
    setError("");
    try {
      await deleteSkill(skill.name);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyName(null);
    }
  }

  if (skills.length === 0) {
    return (
      <div className="skill-market-empty">
        <Package size={26} />
        <span>还没有安装任何技能。去技能市场看看吧。</span>
      </div>
    );
  }

  return (
    <div className="installed-list">
      {error ? <p className="skills-feedback is-error">{error}</p> : null}
      {filtered.map((skill) => (
        <article key={skill.name} className={`skill-row ${skill.enabled ? "" : "is-disabled"}`}>
          <div className="skill-row-main">
            <div className="skill-row-info">
              <div className="skill-row-name">
                <strong>{skill.name}</strong>
                <span className={`skill-badge ${skill.source}`}>
                  {skill.source === "builtin" ? (
                    <>
                      <ShieldCheck size={11} /> 内建
                    </>
                  ) : (
                    <>
                      <Package size={11} /> {skill.source === "market" ? "市场" : "导入"}
                    </>
                  )}
                </span>
              </div>
              <p className="skill-row-desc">{skill.description || "（无描述）"}</p>
            </div>
            <div className="skill-row-actions">
              <button
                className={`skill-toggle ${skill.enabled ? "is-on" : ""}`}
                type="button"
                role="switch"
                aria-checked={skill.enabled}
                onClick={() => toggle(skill)}
                disabled={busyName === skill.name}
                title={skill.enabled ? "点击禁用" : "点击启用"}
              >
                {busyName === skill.name ? (
                  <Loader2 className="spin" size={13} />
                ) : (
                  <span className="skill-toggle-knob" />
                )}
              </button>
              <a
                className="skills-icon-button subtle"
                href={skillExportUrl(skill.name)}
                title="导出为 .zip"
                aria-label={`导出 ${skill.name}`}
              >
                <Download size={16} />
              </a>
              {skill.source !== "builtin" ? (
                <button
                  className="skills-icon-button subtle danger"
                  type="button"
                  onClick={() => remove(skill)}
                  disabled={busyName === skill.name}
                  title="删除技能"
                  aria-label={`删除 ${skill.name}`}
                >
                  <Trash2 size={16} />
                </button>
              ) : null}
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

function formatCount(value: number): string {
  if (value >= 1000) {
    return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k`;
  }
  return String(value);
}
