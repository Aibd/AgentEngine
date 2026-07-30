import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

export type Locale = "zh" | "en";

const STORAGE_KEY = "app.locale";

function loadLocale(): Locale {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "zh" || v === "en") return v;
  } catch { /* noop */ }
  return "zh";
}

function saveLocale(locale: Locale) {
  try { localStorage.setItem(STORAGE_KEY, locale); } catch { /* noop */ }
}

// ---- translations --------------------------------------------------------

const ZH: Record<string, string> = {
  "nav.skills": "技能",
  "nav.newTask": "新建会话",
  "nav.assistant": "助理",
  "nav.experts": "专家",
  "nav.connectors": "连接器",
  "nav.automation": "自动化",
  "nav.more": "更多",
  "nav.tasks": "任务",
  "nav.spaces": "空间",
  "nav.collapse": "收起侧栏",
  "nav.search": "搜索",
  "nav.filter": "筛选",
  "nav.deleteTask": "删除任务",

  "user.menu": "用户菜单",
  "user.name": "用户",
  "user.settings": "设置",
  "user.language": "语言",
  "user.usage": "额度",
  "user.learnMore": "学习更多",
  "user.signOut": "退出",
};

const EN: Record<string, string> = {
  "nav.newTask": "New task",
  "nav.assistant": "Assistant",
  "nav.skills": "Skills",
  "nav.experts": "Experts",
  "nav.connectors": "Connectors",
  "nav.automation": "Automation",
  "nav.more": "More",
  "nav.tasks": "Tasks",
  "nav.spaces": "Spaces",
  "nav.collapse": "Collapse sidebar",
  "nav.search": "Search",
  "nav.filter": "Filter",
  "nav.deleteTask": "Delete task",

  "user.menu": "User menu",
  "user.name": "User",
  "user.settings": "Settings",
  "user.language": "Language",
  "user.usage": "Usage",
  "user.learnMore": "Learn more",
  "user.signOut": "Sign out",
};

const LOCALE_MAP: Record<Locale, Record<string, string>> = { zh: ZH, en: EN };

// ---- context --------------------------------------------------------------

type I18nContextValue = {
  locale: Locale;
  t: (key: string) => string;
  toggleLocale: () => void;
};

const I18nContext = createContext<I18nContextValue>({
  locale: "zh",
  t: (key: string) => key,
  toggleLocale: () => {},
});

export function useI18n() {
  return useContext(I18nContext);
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>(loadLocale);

  const t = useCallback(
    (key: string) => LOCALE_MAP[locale][key] ?? key,
    [locale],
  );

  const toggleLocale = useCallback(() => {
    setLocale((prev) => {
      const next = prev === "zh" ? "en" : "zh";
      saveLocale(next);
      return next;
    });
  }, []);

  return (
    <I18nContext.Provider value={{ locale, t, toggleLocale }}>
      {children}
    </I18nContext.Provider>
  );
}

/** Return the locale label as a display string (e.g. "中文" / "English"). */
export function localeLabel(locale: Locale): string {
  return locale === "zh" ? "中文" : "English";
}
