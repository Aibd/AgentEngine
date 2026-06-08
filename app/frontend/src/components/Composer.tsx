import { DragEvent, FormEvent, useEffect, useRef, useState } from "react";
import {
  BarChart3,
  ChevronDown,
  FileText,
  Loader2,
  Mic,
  Paperclip,
  Plus,
  Send,
  Settings2,
  Sparkles,
  X,
} from "lucide-react";

// A skill selection is either the sentinel "chat" (no specific skill) or a
// skill name surfaced by the backend registry.
export type ComposerSkill = string;
export type ThinkingMode = "fast" | "auto";

export type ComposerSkillOption = {
  name: string;
  description: string;
};

export type ComposerFile = {
  id: string;
  filename: string;
  size_bytes: number;
  kind: "pdf" | "image" | "sheet" | "other";
  previewUrl?: string;
};

type ComposerProps = {
  variant: "center" | "docked";
  query: string;
  onQueryChange: (value: string) => void;
  onSubmit: () => void;
  isRunning: boolean;
  onStop: () => void;
  skill: ComposerSkill;
  onSkillChange: (value: ComposerSkill) => void;
  skillOptions: ComposerSkillOption[];
  onManageSkills: () => void;
  thinkingMode: ThinkingMode;
  onThinkingModeChange: (value: ThinkingMode) => void;
  files: ComposerFile[];
  onAttach: (files: FileList | File[]) => void;
  onRemoveFile: (id: string) => void;
  uploading: boolean;
  uploadError: string;
  canSubmit: boolean;
};

const CHAT_SKILL = "chat";

const ACCEPT_TYPES = ".csv,.xlsx,.pdf,.png,.jpg,.jpeg,.gif,.webp";

export function Composer(props: ComposerProps) {
  const {
    variant,
    query,
    onQueryChange,
    onSubmit,
    isRunning,
    onStop,
    skill,
    onSkillChange,
    skillOptions,
    onManageSkills,
    thinkingMode,
    onThinkingModeChange,
    files,
    onAttach,
    onRemoveFile,
    uploading,
    uploadError,
    canSubmit,
  } = props;

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const recognitionRef = useRef<any>(null);

  useEffect(() => {
    if (!menuOpen) {
      return;
    }

    function closeMenuOnOutsidePointer(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Element)) {
        setMenuOpen(false);
        return;
      }
      if (target.closest(".composer-menu, .composer-plus, .composer-skill-pill")) {
        return;
      }
      setMenuOpen(false);
    }

    function closeMenuOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMenuOpen(false);
      }
    }

    document.addEventListener("pointerdown", closeMenuOnOutsidePointer);
    document.addEventListener("keydown", closeMenuOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeMenuOnOutsidePointer);
      document.removeEventListener("keydown", closeMenuOnEscape);
    };
  }, [menuOpen]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isRunning) {
      onStop();
      return;
    }
    onSubmit();
  }

  function toggleRecording() {
    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      return;
    }

    if (isRecording && recognitionRef.current) {
      recognitionRef.current.stop();
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = "zh-CN";
    recognition.interimResults = false;
    recognition.continuous = false;
    recognitionRef.current = recognition;

    recognition.onresult = (event: any) => {
      const transcript = event.results[0][0].transcript;
      if (transcript) {
        onQueryChange(query ? query + " " + transcript : transcript);
      }
    };

    recognition.onend = () => {
      setIsRecording(false);
      recognitionRef.current = null;
    };

    recognition.onerror = () => {
      setIsRecording(false);
      recognitionRef.current = null;
    };

    recognition.start();
    setIsRecording(true);
  }

  function onDrag(event: DragEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isRunning) {
      setDragActive(true);
    }
  }

  function onDragLeave(event: DragEvent<HTMLFormElement>) {
    event.preventDefault();
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
      return;
    }
    setDragActive(false);
  }

  function onDrop(event: DragEvent<HTMLFormElement>) {
    event.preventDefault();
    setDragActive(false);
    if (!isRunning && event.dataTransfer.files.length) {
      onAttach(event.dataTransfer.files);
    }
  }

  function pickFiles() {
    setMenuOpen(false);
    fileInputRef.current?.click();
  }

  function chooseSkill(next: ComposerSkill) {
    // Toggle off when re-selecting the active skill.
    onSkillChange(skill === next ? CHAT_SKILL : next);
    setMenuOpen(false);
  }

  const activeSkillOption = skillOptions.find((option) => option.name === skill);
  const hasActiveSkill = skill !== CHAT_SKILL;

  const className = [
    "composer-shell",
    variant === "center" ? "composer-center" : "composer-docked",
    hasActiveSkill ? "has-active-skill" : "",
    dragActive ? "is-dragging" : "",
    isRunning ? "is-running" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <form
      className={className}
      onSubmit={handleSubmit}
      onDragEnter={onDrag}
      onDragOver={onDrag}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPT_TYPES}
        multiple
        hidden
        onChange={(event) => {
          if (event.target.files) {
            onAttach(event.target.files);
            event.target.value = "";
          }
        }}
      />

      {files.length ? (
        <div className="composer-files" aria-label="已上传文件">
          {files.map((file) => (
            <FileChip key={file.id} file={file} onRemove={() => onRemoveFile(file.id)} />
          ))}
        </div>
      ) : null}

      <div className="composer-input-row">
        <button
          className={menuOpen ? "composer-plus is-open" : "composer-plus"}
          type="button"
          onClick={() => setMenuOpen((value) => !value)}
          disabled={isRunning || uploading}
          title="Add files or choose a skill"
          aria-label="Add files or choose a skill"
        >
          {uploading ? <Loader2 className="spin" size={18} /> : <Plus size={18} />}
        </button>

        {menuOpen ? (
          <div className="composer-menu" role="menu">
            <button type="button" role="menuitem" onClick={pickFiles}>
              <Paperclip size={16} />
              <span>Add photos & files</span>
            </button>
            <div className="composer-menu-separator" role="separator" />
            <div className="composer-menu-label">技能</div>
            {skillOptions.length === 0 ? (
              <div className="composer-menu-empty">暂无已启用技能</div>
            ) : (
              skillOptions.map((option) => (
                <button
                  key={option.name}
                  type="button"
                  role="menuitemradio"
                  aria-checked={skill === option.name}
                  className={skill === option.name ? "is-active" : ""}
                  onClick={() => chooseSkill(option.name)}
                  title={option.description}
                >
                  {option.name === "data_analysis" ? (
                    <BarChart3 size={16} />
                  ) : (
                    <Sparkles size={16} />
                  )}
                  <span>{skillLabel(option)}</span>
                </button>
              ))
            )}
            <div className="composer-menu-separator" role="separator" />
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                onManageSkills();
              }}
            >
              <Settings2 size={16} />
              <span>管理技能…</span>
            </button>
          </div>
        ) : null}

        <input
          className="composer-text"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Ask anything"
          disabled={isRunning}
        />

        {hasActiveSkill ? (
          <button className="composer-skill-pill" type="button" onClick={() => setMenuOpen(true)}>
            {skill === "data_analysis" ? <BarChart3 size={15} /> : <Sparkles size={15} />}
            <span>{activeSkillOption ? skillLabel(activeSkillOption) : skill}</span>
            <ChevronDown size={13} />
          </button>
        ) : null}

        {uploadError ? <span className="composer-upload-error">{uploadError}</span> : null}

        <div className="composer-spacer" />

        <ThinkingModeSelect
          value={thinkingMode}
          onChange={onThinkingModeChange}
          disabled={isRunning}
        />
        <button
          className={isRecording ? "composer-mic is-recording" : "composer-mic"}
          type="button"
          onClick={toggleRecording}
          title={isRecording ? "停止语音" : "语音输入"}
          aria-label={isRecording ? "停止语音" : "语音输入"}
          disabled={isRunning}
        >
          <Mic size={16} />
        </button>
        <button
          className={isRunning ? "composer-send is-running" : "composer-send"}
          type={isRunning ? "button" : "submit"}
          onClick={isRunning ? onStop : undefined}
          disabled={!isRunning && !canSubmit}
          title={isRunning ? "停止" : "发送"}
          aria-label={isRunning ? "停止" : "发送"}
        >
          {isRunning ? <Loader2 className="spin" size={16} /> : <Send size={16} />}
        </button>
      </div>
    </form>
  );
}

function ThinkingModeSelect({
  value,
  onChange,
  disabled,
}: {
  value: ThinkingMode;
  onChange: (next: ThinkingMode) => void;
  disabled: boolean;
}) {
  return (
    <label className="composer-thinking" title="思考模式">
      <span>{labelForThinkingMode(value)}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as ThinkingMode)}
        disabled={disabled}
      >
        <option value="fast">Fast</option>
        <option value="auto">Thinking</option>
      </select>
    </label>
  );
}

function FileChip({ file, onRemove }: { file: ComposerFile; onRemove: () => void }) {
  if (file.kind === "image") {
    return (
      <div className="file-chip is-image" title={file.filename}>
        {file.previewUrl ? (
          <img src={file.previewUrl} alt={file.filename} />
        ) : (
          <div className="file-chip-image-fallback" />
        )}
        <button
          type="button"
          className="file-chip-remove on-image"
          onClick={onRemove}
          aria-label={`移除 ${file.filename}`}
        >
          <X size={12} />
        </button>
      </div>
    );
  }

  const iconBgClass = file.kind === "pdf" ? "is-pdf" : file.kind === "sheet" ? "is-sheet" : "is-other";
  const label = file.kind === "pdf" ? "PDF" : file.kind === "sheet" ? "Sheet" : "File";

  return (
    <div className="file-chip is-doc" title={file.filename}>
      <div className={`file-chip-icon ${iconBgClass}`}>
        <FileText size={18} />
      </div>
      <div className="file-chip-meta">
        <strong>{file.filename}</strong>
        <span>{label}</span>
      </div>
      <button
        type="button"
        className="file-chip-remove"
        onClick={onRemove}
        aria-label={`移除 ${file.filename}`}
      >
        <X size={12} />
      </button>
    </div>
  );
}

function labelForThinkingMode(value: ThinkingMode): string {
  return value === "fast" ? "Fast" : "Thinking";
}

const SKILL_DISPLAY_NAMES: Record<string, string> = {
  data_analysis: "数据分析",
};

function skillLabel(option: ComposerSkillOption): string {
  return SKILL_DISPLAY_NAMES[option.name] ?? option.name;
}

export function detectFileKind(filename: string): ComposerFile["kind"] {
  const ext = filename.toLowerCase().split(".").pop() ?? "";
  if (ext === "pdf") {
    return "pdf";
  }
  if (["png", "jpg", "jpeg", "gif", "webp"].includes(ext)) {
    return "image";
  }
  if (["csv", "xlsx", "xls"].includes(ext)) {
    return "sheet";
  }
  return "other";
}
