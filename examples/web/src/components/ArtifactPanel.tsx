import { useEffect, useRef, useState } from "react";
import { ChevronDown, Code2, Download, Eye, FileCode2, FileText, X } from "lucide-react";
import type { ArtifactTrace } from "../types";

type ArtifactPanelProps = {
  artifact: ArtifactTrace;
  onClose: () => void;
};

export function ArtifactPanel({ artifact, onClose }: ArtifactPanelProps) {
  const [mode, setMode] = useState<"preview" | "code">("preview");
  const codeWrapRef = useRef<HTMLDivElement | null>(null);
  const hasHtml = Boolean(artifact.html.trim());

  useEffect(() => {
    if (mode !== "code" || !codeWrapRef.current) {
      return;
    }
    codeWrapRef.current.scrollTop = codeWrapRef.current.scrollHeight;
  }, [artifact.html, mode]);

  return (
    <aside className="artifact-panel" aria-label="HTML report preview">
      <header className="artifact-header">
        <div className="artifact-title">
          <FileText size={18} />
          <div>
            <strong>{artifact.title}</strong>
            <span>{artifact.status === "ready" ? "Ready" : artifact.currentSection || "Rendering HTML"}</span>
          </div>
        </div>
        <div className="artifact-actions">
          <div className="artifact-view-toggle" role="group" aria-label="报告查看模式">
            <button
              className={mode === "preview" ? "artifact-toggle-button is-active" : "artifact-toggle-button"}
              type="button"
              aria-label="预览"
              aria-pressed={mode === "preview"}
              title="预览"
              onClick={() => setMode("preview")}
            >
              <Eye size={15} />
            </button>
            <button
              className={mode === "code" ? "artifact-toggle-button is-active" : "artifact-toggle-button"}
              type="button"
              aria-label="源码"
              aria-pressed={mode === "code"}
              title="源码"
              onClick={() => setMode("code")}
            >
              <FileCode2 size={15} />
            </button>
          </div>
          <ExportMenu artifact={artifact} />
          <button className="artifact-icon-button" type="button" title="关闭预览" onClick={onClose}>
            <X size={17} />
          </button>
        </div>
      </header>

      <div className="artifact-html-wrap">
        {artifact.status === "failed" ? (
          <div className="report-empty is-error">
            <Code2 size={22} />
            <span>{artifact.error || "HTML report generation failed."}</span>
          </div>
        ) : mode === "code" ? (
          <div className="artifact-code-wrap" ref={codeWrapRef}>
            <pre className="artifact-code"><code>{artifact.html || "<!-- waiting for streamed HTML -->"}</code></pre>
          </div>
        ) : hasHtml ? (
          <iframe
            className="artifact-html-frame"
            title={artifact.title}
            sandbox=""
            srcDoc={artifact.html}
          />
        ) : (
          <div className="report-empty">
            <Code2 size={22} />
            <span>正在等待 HTML 内容...</span>
          </div>
        )}
      </div>
    </aside>
  );
}

function ExportMenu({ artifact }: { artifact: ArtifactTrace }) {
  const entries = Object.entries(artifact.exports ?? {});
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handlePointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  if (!entries.length) {
    return (
      <button className="artifact-export" type="button" disabled>
        <Download size={15} />
        <span>Download</span>
      </button>
    );
  }
  return (
    <div className="artifact-download" ref={menuRef}>
      <button className="artifact-export" type="button" onClick={() => setOpen((value) => !value)}>
        <Download size={15} />
        <span>Download</span>
        <ChevronDown size={14} />
      </button>
      {open ? (
        <div className="artifact-download-menu" role="menu">
          {entries.map(([format, href]) => (
            <a
              className="artifact-download-item"
              key={format}
              href={href}
              download
              role="menuitem"
              onClick={() => setOpen(false)}
            >
              <Download size={14} />
              <span>{formatLabel(format)}</span>
            </a>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function formatLabel(format: string) {
  if (format === "md") {
    return "Markdown";
  }
  if (format === "word") {
    return "Word";
  }
  return format.toUpperCase();
}
