import { DragEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  Package,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import {
  deleteSkill,
  fetchSkillDetail,
  importSkillZip,
  setSkillEnabled,
  skillExportUrl,
} from "../traceTransport";
import type { SkillDetail, SkillSummary } from "../types";

type SkillsManagerProps = {
  skills: SkillSummary[];
  onClose: () => void;
  onChanged: () => void;
};

export function SkillsManager({ skills, onClose, onChanged }: SkillsManagerProps) {
  const [busyName, setBusyName] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    function onEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }
    document.addEventListener("keydown", onEscape);
    return () => document.removeEventListener("keydown", onEscape);
  }, [onClose]);

  const sorted = useMemo(
    () => [...skills].sort((a, b) => a.name.localeCompare(b.name)),
    [skills],
  );
  const enabledCount = sorted.filter((skill) => skill.enabled).length;

  async function handleToggle(skill: SkillSummary) {
    setBusyName(skill.name);
    setError("");
    setNotice("");
    try {
      await setSkillEnabled(skill.name, !skill.enabled);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyName(null);
    }
  }

  async function handleDelete(skill: SkillSummary) {
    if (!window.confirm(`确定删除技能「${skill.name}」？该操作会从磁盘移除其文件。`)) {
      return;
    }
    setBusyName(skill.name);
    setError("");
    setNotice("");
    try {
      await deleteSkill(skill.name);
      if (expanded === skill.name) {
        setExpanded(null);
      }
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyName(null);
    }
  }

  async function handleFiles(files: FileList | File[]) {
    const file = Array.from(files).find((item) => item.name.toLowerCase().endsWith(".zip"));
    if (!file) {
      setError("请上传 .zip 格式的技能包");
      return;
    }
    setImporting(true);
    setError("");
    setNotice("");
    try {
      const imported = await importSkillZip(file);
      setNotice(`已导入技能「${imported.name}」`);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setImporting(false);
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    if (event.dataTransfer.files.length) {
      void handleFiles(event.dataTransfer.files);
    }
  }

  return (
    <div className="skills-overlay" role="dialog" aria-modal="true" aria-label="技能管理">
      <div className="skills-modal">
        <header className="skills-modal-head">
          <div className="skills-modal-title">
            <Sparkles size={18} />
            <div>
              <strong>技能管理</strong>
              <span>
                {sorted.length} 个技能 · {enabledCount} 个已启用
              </span>
            </div>
          </div>
          <button className="skills-icon-button" type="button" onClick={onClose} title="关闭" aria-label="关闭">
            <X size={18} />
          </button>
        </header>

        <div
          className={dragActive ? "skills-import is-dragging" : "skills-import"}
          onDragOver={(event) => {
            event.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={(event) => {
            event.preventDefault();
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
              setDragActive(false);
            }
          }}
          onDrop={onDrop}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip"
            hidden
            onChange={(event) => {
              if (event.target.files) {
                void handleFiles(event.target.files);
                event.target.value = "";
              }
            }}
          />
          <div className="skills-import-icon">
            {importing ? <Loader2 className="spin" size={20} /> : <Upload size={20} />}
          </div>
          <div className="skills-import-text">
            <strong>导入社区技能</strong>
            <span>拖入或选择一个包含 SKILL.md 的 .zip 包</span>
          </div>
          <button
            className="skills-import-button"
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
          >
            选择文件
          </button>
        </div>

        {error ? <p className="skills-feedback is-error">{error}</p> : null}
        {notice ? <p className="skills-feedback is-ok">{notice}</p> : null}

        <div className="skills-list">
          {sorted.length === 0 ? (
            <div className="skills-empty">
              <Package size={26} />
              <span>还没有技能。导入一个 .zip 技能包开始吧。</span>
            </div>
          ) : (
            sorted.map((skill) => (
              <SkillRow
                key={skill.name}
                skill={skill}
                busy={busyName === skill.name}
                expanded={expanded === skill.name}
                onToggleExpand={() =>
                  setExpanded((current) => (current === skill.name ? null : skill.name))
                }
                onToggleEnabled={() => handleToggle(skill)}
                onDelete={() => handleDelete(skill)}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function SkillRow({
  skill,
  busy,
  expanded,
  onToggleExpand,
  onToggleEnabled,
  onDelete,
}: {
  skill: SkillSummary;
  busy: boolean;
  expanded: boolean;
  onToggleExpand: () => void;
  onToggleEnabled: () => void;
  onDelete: () => void;
}) {
  return (
    <article className={`skill-row ${skill.enabled ? "" : "is-disabled"}`}>
      <div className="skill-row-main">
        <button
          className="skill-row-expand"
          type="button"
          onClick={onToggleExpand}
          aria-label={expanded ? "收起详情" : "展开详情"}
        >
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        <div className="skill-row-info">
          <div className="skill-row-name">
            <strong>{skill.name}</strong>
            <span className={`skill-badge ${skill.source}`}>
              {skill.source === "imported" ? (
                <>
                  <Package size={11} /> 导入
                </>
              ) : (
                <>
                  <ShieldCheck size={11} /> 内建
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
            onClick={onToggleEnabled}
            disabled={busy}
            title={skill.enabled ? "点击禁用" : "点击启用"}
          >
            {busy ? <Loader2 className="spin" size={13} /> : <span className="skill-toggle-knob" />}
          </button>
          <a
            className="skills-icon-button subtle"
            href={skillExportUrl(skill.name)}
            title="导出为 .zip"
            aria-label={`导出 ${skill.name}`}
          >
            <Download size={16} />
          </a>
          {skill.source === "imported" ? (
            <button
              className="skills-icon-button subtle danger"
              type="button"
              onClick={onDelete}
              disabled={busy}
              title="删除技能"
              aria-label={`删除 ${skill.name}`}
            >
              <Trash2 size={16} />
            </button>
          ) : null}
        </div>
      </div>
      {expanded ? <SkillRowDetail name={skill.name} /> : null}
    </article>
  );
}

function SkillRowDetail({ name }: { name: string }) {
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError("");
    fetchSkillDetail(name)
      .then((value) => {
        if (!cancelled) {
          setDetail(value);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [name]);

  if (error) {
    return <div className="skill-row-detail is-error">{error}</div>;
  }
  if (!detail) {
    return (
      <div className="skill-row-detail">
        <Loader2 className="spin" size={14} /> 加载中…
      </div>
    );
  }
  return (
    <div className="skill-row-detail">
      {detail.origin ? <p className="skill-detail-origin">来源：{detail.origin}</p> : null}
      <pre className="skill-detail-body">{detail.body.trim() || "（SKILL.md 正文为空）"}</pre>
    </div>
  );
}
