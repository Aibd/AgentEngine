import { FileSpreadsheet, Loader2, Upload } from "lucide-react";
import { DragEvent, useRef, useState } from "react";
import { uploadReportFile } from "../traceTransport";
import type { ReportFileSummary } from "../types";

type FileUploadPanelProps = {
  conversationId: string;
  files: ReportFileSummary[];
  onUploaded: (file: ReportFileSummary) => void;
};

export function FileUploadPanel({ conversationId, files, onUploaded }: FileUploadPanelProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState("");

  async function handleFiles(fileList: FileList | null) {
    const selectedFiles = Array.from(fileList ?? []).filter((file) => file.name.trim());
    if (!selectedFiles.length) {
      return;
    }
    setUploading(true);
    setError("");
    try {
      const uploaded = await Promise.all(
        selectedFiles.map((file) => uploadReportFile(file, conversationId)),
      );
      uploaded.forEach(onUploaded);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
      setDragActive(false);
      if (inputRef.current) {
        inputRef.current.value = "";
      }
    }
  }

  function onDrag(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragActive(true);
  }

  function onDragLeave(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
      return;
    }
    setDragActive(false);
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    void handleFiles(event.dataTransfer.files);
  }

  const latest = files.at(-1);
  const firstSheet = latest?.parsed.sheets[0];

  return (
    <section
      className={dragActive ? "file-upload-panel is-dragging" : "file-upload-panel"}
      aria-label="Financial report upload"
      onDragEnter={onDrag}
      onDragOver={onDrag}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <div className="file-upload-head">
        <FileSpreadsheet size={18} />
        <div>
          <strong>财务文件</strong>
          <span>支持 CSV / XLSX 预览解析</span>
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".csv,.xlsx"
        multiple
        onChange={(event) => void handleFiles(event.target.files)}
        hidden
      />
      <button
        className="file-upload-button"
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={uploading}
      >
        {uploading ? <Loader2 className="spin" size={16} /> : <Upload size={16} />}
        <span>{uploading ? "解析中" : "上传文件"}</span>
      </button>
      {error ? <p className="file-upload-error">{error}</p> : null}
      {latest ? (
        <div className="file-preview-card">
          <strong>{latest.filename}</strong>
          <span>
            {(latest.size_bytes / 1024).toFixed(1)} KB / {latest.parsed.sheets.length} sheet
          </span>
          {firstSheet ? (
            <div className="file-preview-table">
              <div>
                {firstSheet.headers.slice(0, 5).map((header, index) => (
                  <b key={`${header}-${index}`}>{header || "-"}</b>
                ))}
              </div>
              {firstSheet.rows.slice(0, 3).map((row, index) => (
                <div key={index}>
                  {row.slice(0, 5).map((cell, cellIndex) => (
                    <span key={`${index}-${cellIndex}`}>{cell || "-"}</span>
                  ))}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
