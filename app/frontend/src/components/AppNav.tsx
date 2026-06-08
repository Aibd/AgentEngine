import { useState, type ReactNode } from "react";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  Clock3,
  Grid2x2,
  PanelLeftClose,
  Plus,
  Search,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Users,
} from "lucide-react";

export type AppView =
  | "chat"
  | "skills"
  | "experts"
  | "connectors"
  | "automation"
  | "more";

export type NavSession = {
  id: string;
  title: string;
};

export type NavSpace = {
  id: string;
  name: string;
  sessionIds: string[];
};

type AppNavProps = {
  version: string;
  activeView: AppView;
  onNavigate: (view: AppView) => void;
  onNewTask: () => void;
  onCollapse: () => void;
  sessions: NavSession[];
  spaces: NavSpace[];
  activeSessionId: string;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  footer?: ReactNode;
};

const NAV_ITEMS: Array<{
  view: AppView;
  label: string;
  hint: string;
  icon: ReactNode;
}> = [
  { view: "chat", label: "新建任务", hint: "", icon: <Plus size={18} /> },
  { view: "chat", label: "助理", hint: "Claw", icon: <Bot size={18} /> },
  { view: "experts", label: "专家", hint: "技能/连接器", icon: <Users size={18} /> },
  { view: "automation", label: "自动化", hint: "定时任务", icon: <Clock3 size={18} /> },
  { view: "more", label: "更多", hint: "资料库·灵感", icon: <Grid2x2 size={18} /> },
];

export function AppNav(props: AppNavProps) {
  const {
    version,
    activeView,
    onNavigate,
    onNewTask,
    onCollapse,
    sessions,
    spaces,
    activeSessionId,
    onSelectSession,
    onDeleteSession,
    footer,
  } = props;

  const [tasksOpen, setTasksOpen] = useState(true);
  const [spacesOpen, setSpacesOpen] = useState(true);

  function handleNavClick(item: (typeof NAV_ITEMS)[number]) {
    if (item.label === "新建任务") {
      onNewTask();
      return;
    }
    onNavigate(item.view);
  }

  return (
    <aside className="app-nav" aria-label="主导航">
      <div className="app-nav-brand">
        <div className="app-nav-logo">
          <Sparkles size={18} />
          <div>
            <strong>AgentEngine</strong>
            <span>v{version}</span>
          </div>
        </div>
        <div className="app-nav-brand-actions">
          <button type="button" className="app-nav-icon" onClick={onCollapse} title="收起侧栏" aria-label="收起侧栏">
            <PanelLeftClose size={17} />
          </button>
          <button type="button" className="app-nav-icon" title="搜索" aria-label="搜索">
            <Search size={17} />
          </button>
          <button type="button" className="app-nav-icon" title="筛选" aria-label="筛选">
            <SlidersHorizontal size={17} />
          </button>
        </div>
      </div>

      <nav className="app-nav-rows">
        {NAV_ITEMS.map((item, index) => {
          const isActive =
            item.label !== "新建任务" && activeView === item.view;
          return (
            <button
              key={`${item.label}-${index}`}
              type="button"
              className={isActive ? "nav-row is-active" : "nav-row"}
              onClick={() => handleNavClick(item)}
            >
              <span className="nav-row-icon">{item.icon}</span>
              <span className="nav-row-label">{item.label}</span>
              {item.hint ? <span className="nav-row-hint">{item.hint}</span> : null}
            </button>
          );
        })}
      </nav>

      <div className="app-nav-scroll">
        <NavGroup
          title={`任务 (${sessions.length})`}
          open={tasksOpen}
          onToggle={() => setTasksOpen((value) => !value)}
        >
          {sessions.map((session) => (
            <NavSessionRow
              key={session.id}
              session={session}
              active={session.id === activeSessionId && activeView === "chat"}
              onSelect={() => onSelectSession(session.id)}
              onDelete={() => onDeleteSession(session.id)}
            />
          ))}
        </NavGroup>

        <NavGroup
          title={`空间 (${spaces.length})`}
          open={spacesOpen}
          onToggle={() => setSpacesOpen((value) => !value)}
        >
          {spaces.map((space) => (
            <div key={space.id} className="nav-space">
              <span className="nav-space-name">{space.name}</span>
            </div>
          ))}
        </NavGroup>
      </div>

      {footer ? <div className="app-nav-footer">{footer}</div> : null}
    </aside>
  );
}

function NavGroup({
  title,
  open,
  onToggle,
  children,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className="nav-group">
      <button type="button" className="nav-group-head" onClick={onToggle}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>{title}</span>
      </button>
      {open ? <div className="nav-group-body">{children}</div> : null}
    </section>
  );
}

function NavSessionRow({
  session,
  active,
  onSelect,
  onDelete,
}: {
  session: NavSession;
  active: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  return (
    <div className={active ? "nav-session is-active" : "nav-session"}>
      <button type="button" className="nav-session-title" onClick={onSelect} title={session.title}>
        {session.title}
      </button>
      <button
        type="button"
        className="nav-session-delete"
        onClick={onDelete}
        title="删除任务"
        aria-label={`删除任务 ${session.title}`}
      >
        <Trash2 size={13} />
      </button>
    </div>
  );
}
