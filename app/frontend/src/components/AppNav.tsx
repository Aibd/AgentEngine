import { useState, useRef, useEffect, type ReactNode } from "react";
import {
  Bot,
  Cable,
  ChevronDown,
  ChevronRight,
  CircleUserRound,
  Clock3,
  Grid2x2,
  Globe,
  HelpCircle,
  LogOut,
  PanelLeftClose,
  Plus,
  Search,
  Settings,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Users,
  Zap,
} from "lucide-react";
import { useI18n, localeLabel } from "../i18n";

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
  onOpenSettings?: () => void;
  onLogout?: () => void;
};

const NAV_ITEMS: Array<{
  view: AppView;
  labelKey: string;
  hint: string;
  icon: ReactNode;
}> = [
  { view: "chat", labelKey: "nav.newTask", hint: "", icon: <Plus size={18} /> },
  { view: "chat", labelKey: "nav.assistant", hint: "默认", icon: <Bot size={18} /> },
  { view: "experts", labelKey: "nav.experts", hint: "技能", icon: <Users size={18} /> },
  { view: "connectors", labelKey: "nav.connectors", hint: "MCP", icon: <Cable size={18} /> },
  { view: "automation", labelKey: "nav.automation", hint: "定时任务", icon: <Clock3 size={18} /> },
  { view: "more", labelKey: "nav.more", hint: "资料库·灵感", icon: <Grid2x2 size={18} /> },
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
    onOpenSettings,
    onLogout,
  } = props;

  const { locale, t, toggleLocale } = useI18n();
  const [tasksOpen, setTasksOpen] = useState(true);
  const [spacesOpen, setSpacesOpen] = useState(true);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);

  const langLabel = localeLabel(locale);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setUserMenuOpen(false);
      }
    }
    if (userMenuOpen) {
      document.addEventListener("mousedown", onClick);
      return () => document.removeEventListener("mousedown", onClick);
    }
  }, [userMenuOpen]);

  function handleMenuAction(action: string) {
    switch (action) {
      case "settings":
        setUserMenuOpen(false);
        onOpenSettings?.();
        break;
      case "locale":
        toggleLocale();
        // Keep the popover open so the user sees the language switch.
        break;
      case "usage":
        setUserMenuOpen(false);
        // TODO: navigate to usage/billing page
        break;
      case "learn":
        setUserMenuOpen(false);
        window.open("https://github.com/agentengine/docs", "_blank", "noopener");
        break;
      case "logout":
        setUserMenuOpen(false);
        onLogout?.();
        break;
    }
  }

  function handleNavClick(item: (typeof NAV_ITEMS)[number]) {
    if (item.labelKey === "nav.newTask") {
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
          <button type="button" className="app-nav-icon" onClick={onCollapse} title={t("nav.collapse")} aria-label={t("nav.collapse")}>
            <PanelLeftClose size={17} />
          </button>
          <button type="button" className="app-nav-icon" title={t("nav.search")} aria-label={t("nav.search")}>
            <Search size={17} />
          </button>
          <button type="button" className="app-nav-icon" title={t("nav.filter")} aria-label={t("nav.filter")}>
            <SlidersHorizontal size={17} />
          </button>
        </div>
      </div>

      <nav className="app-nav-rows">
        {NAV_ITEMS.map((item, index) => {
          const isActive =
            item.labelKey !== "nav.newTask" && activeView === item.view;
          return (
            <button
              key={`${item.labelKey}-${index}`}
              type="button"
              className={isActive ? "nav-row is-active" : "nav-row"}
              onClick={() => handleNavClick(item)}
            >
              <span className="nav-row-icon">{item.icon}</span>
              <span className="nav-row-label">{t(item.labelKey)}</span>
              {item.hint ? <span className="nav-row-hint">{item.hint}</span> : null}
            </button>
          );
        })}
      </nav>

      <div className="app-nav-scroll">
        <NavGroup
          title={`${t("nav.tasks")} (${sessions.length})`}
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
          title={`${t("nav.spaces")} (${spaces.length})`}
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

      <div className="app-nav-footer">
        <div className="app-nav-user" ref={userMenuRef}>
          <button
            type="button"
            className="app-nav-user-btn"
            onClick={() => setUserMenuOpen((v) => !v)}
            title={t("user.menu")}
          >
            <span className="app-nav-user-avatar">
              <CircleUserRound size={22} />
            </span>
            <span className="app-nav-user-name">
              {t("user.name")}
            </span>
          </button>
          {userMenuOpen ? (
            <div className="app-nav-user-popover">
              <button
                type="button"
                className="user-popover-item"
                onClick={() => handleMenuAction("settings")}
              >
                <Settings size={15} />
                <span>{t("user.settings")}</span>
              </button>
              <button
                type="button"
                className="user-popover-item"
                onClick={() => handleMenuAction("locale")}
              >
                <Globe size={15} />
                <span>{t("user.language")}</span>
                <span className="user-popover-extra">{langLabel}</span>
              </button>
              <button
                type="button"
                className="user-popover-item"
                onClick={() => handleMenuAction("usage")}
              >
                <Zap size={15} />
                <span>{t("user.usage")}</span>
              </button>
              <button
                type="button"
                className="user-popover-item"
                onClick={() => handleMenuAction("learn")}
              >
                <HelpCircle size={15} />
                <span>{t("user.learnMore")}</span>
              </button>
              <div className="user-popover-divider" />
              <button
                type="button"
                className="user-popover-item user-popover-item--danger"
                onClick={() => handleMenuAction("logout")}
              >
                <LogOut size={15} />
                <span>{t("user.signOut")}</span>
              </button>
            </div>
          ) : null}
        </div>
      </div>
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
  const { t } = useI18n();
  return (
    <div className={active ? "nav-session is-active" : "nav-session"}>
      <button type="button" className="nav-session-title" onClick={onSelect} title={session.title}>
        {session.title}
      </button>
      <button
        type="button"
        className="nav-session-delete"
        onClick={onDelete}
        title={t("nav.deleteTask")}
        aria-label={`${t("nav.deleteTask")} ${session.title}`}
      >
        <Trash2 size={13} />
      </button>
    </div>
  );
}
