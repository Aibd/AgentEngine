import { useEffect, useState } from "react";
import { Loader2, Menu, Search, Sparkles, Users } from "lucide-react";
import {
  fetchExpertCategories,
  fetchExpertScenarios,
  fetchExpertTeamCategories,
  fetchExpertTeams,
  fetchExperts,
} from "../traceTransport";
import type { Expert, ExpertTeam, ScenarioGroup } from "../types";
type ExpertsPageProps = {
  onStartExpert: (expert: Expert) => void;
  onStartTeam: (team: ExpertTeam) => void;
  sidebarOpen: boolean;
  onExpandSidebar: () => void;
};

type SubTab = "experts" | "teams";

export function ExpertsPage({
  onStartExpert,
  onStartTeam,
  sidebarOpen,
  onExpandSidebar,
}: ExpertsPageProps) {
  const [subTab, setSubTab] = useState<SubTab>("experts");
  const [search, setSearch] = useState("");

  return (
    <section className="experts-page">
      <header className="skills-page-head">
        {!sidebarOpen ? (
          <button className="skills-page-collapse" type="button" onClick={onExpandSidebar} title="展开侧栏">
            <Menu size={18} />
          </button>
        ) : null}
        <div className="page-section-title">
          <Sparkles size={18} />
          <div>
            <h1>专家</h1>
            <p>选择一个专家或专家团队，开启带有专业指令的会话。</p>
          </div>
        </div>
        <div className="skills-page-head-actions">
          <label className="skills-page-search">
            <Search size={15} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索专家职称或描述"
            />
          </label>
          <button type="button" className="skills-page-add" disabled title="即将上线">
            <Users size={16} /> 我的专家
          </button>
        </div>
      </header>

      {subTab === "experts" && !search ? <ScenarioRail onStartExpert={onStartExpert} /> : null}

      <div className="skills-subtabs">
        <button
          type="button"
          className={subTab === "experts" ? "subtab is-active" : "subtab"}
          onClick={() => setSubTab("experts")}
        >
          专家
        </button>
        <button
          type="button"
          className={subTab === "teams" ? "subtab is-active" : "subtab"}
          onClick={() => setSubTab("teams")}
        >
          专家团
        </button>
      </div>

      {subTab === "experts" ? (
        <ExpertsTab search={search} onStartExpert={onStartExpert} />
      ) : (
        <TeamsTab search={search} onStartTeam={onStartTeam} />
      )}
    </section>
  );
}

function ScenarioRail({ onStartExpert }: { onStartExpert: (expert: Expert) => void }) {
  const [groups, setGroups] = useState<ScenarioGroup[]>([]);

  useEffect(() => {
    fetchExpertScenarios()
      .then(setGroups)
      .catch(() => setGroups([]));
  }, []);

  if (groups.length === 0) {
    return null;
  }

  return (
    <div className="scenario-section">
      <h2 className="scenario-title">精选场景</h2>
      <div className="scenario-rail">
        {groups.map((group) => (
          <article key={group.scenario} className="scenario-card">
            <h3>{group.scenario}</h3>
            <ul>
              {group.experts.map((expert) => (
                <li key={expert.name}>
                  <button type="button" onClick={() => onStartExpert(expert)}>
                    <span className="scenario-expert-avatar">{avatarText(expert)}</span>
                    {expert.role}
                  </button>
                </li>
              ))}
            </ul>
          </article>
        ))}
      </div>
    </div>
  );
}

function ExpertsTab({
  search,
  onStartExpert,
}: {
  search: string;
  onStartExpert: (expert: Expert) => void;
}) {
  const [categories, setCategories] = useState<string[]>(["全部"]);
  const [activeCategory, setActiveCategory] = useState("全部");
  const [experts, setExperts] = useState<Expert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchExpertCategories()
      .then(setCategories)
      .catch(() => setCategories(["全部"]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchExperts(activeCategory, undefined, search)
      .then((list) => {
        if (!cancelled) {
          setExperts(list);
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

  return (
    <div className="experts-body">
      <div className="skill-category-row experts-category-row">
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

      {error ? <p className="skills-feedback is-error">{error}</p> : null}

      {loading ? (
        <div className="skill-market-loading">
          <Loader2 className="spin" size={20} /> 加载中…
        </div>
      ) : experts.length === 0 ? (
        <div className="skill-market-empty">
          <Users size={26} />
          <span>没有匹配的专家。</span>
        </div>
      ) : (
        <div className="expert-grid">
          {experts.map((expert) => (
            <ExpertCard key={expert.name} expert={expert} onStart={() => onStartExpert(expert)} />
          ))}
        </div>
      )}
    </div>
  );
}

function ExpertCard({ expert, onStart }: { expert: Expert; onStart: () => void }) {
  return (
    <button type="button" className="expert-card" onClick={onStart}>
      <div className="expert-card-head">
        <span className="expert-avatar">{avatarText(expert)}</span>
        <div className="expert-card-title">
          <div className="expert-card-role">
            <strong>{expert.role}</strong>
            {expert.badge ? <span className="expert-badge">{expert.badge}</span> : null}
          </div>
          {expert.author ? <span className="expert-card-author">{expert.author}</span> : null}
        </div>
      </div>
      <p className="expert-card-desc">{expert.description}</p>
      {expert.skills.length ? (
        <div className="expert-skill-tags">
          {expert.skills.map((skill) => (
            <span key={skill} className="expert-skill-tag">
              {skill}
            </span>
          ))}
        </div>
      ) : null}
    </button>
  );
}

function TeamsTab({
  search,
  onStartTeam,
}: {
  search: string;
  onStartTeam: (team: ExpertTeam) => void;
}) {
  const [categories, setCategories] = useState<string[]>(["全部"]);
  const [activeCategory, setActiveCategory] = useState("全部");
  const [teams, setTeams] = useState<ExpertTeam[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchExpertTeamCategories()
      .then(setCategories)
      .catch(() => setCategories(["全部"]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchExpertTeams(activeCategory, search)
      .then((list) => {
        if (!cancelled) {
          setTeams(list);
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

  return (
    <div className="experts-body">
      <div className="skill-category-row experts-category-row">
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

      {error ? <p className="skills-feedback is-error">{error}</p> : null}

      {loading ? (
        <div className="skill-market-loading">
          <Loader2 className="spin" size={20} /> 加载中…
        </div>
      ) : teams.length === 0 ? (
        <div className="skill-market-empty">
          <Users size={26} />
          <span>没有匹配的专家团。</span>
        </div>
      ) : (
        <div className="team-grid">
          {teams.map((team) => (
            <TeamCard key={team.id} team={team} onStart={() => onStartTeam(team)} />
          ))}
        </div>
      )}
    </div>
  );
}

function TeamCard({ team, onStart }: { team: ExpertTeam; onStart: () => void }) {
  return (
    <button type="button" className="team-card" onClick={onStart}>
      <div className="team-card-head">
        <div className="team-member-avatars">
          {team.member_experts.map((expert, index) => (
            <span
              key={expert.name}
              className="team-member-avatar"
              style={{ zIndex: team.member_experts.length - index }}
              title={expert.role}
            >
              {avatarText(expert)}
            </span>
          ))}
        </div>
        <strong className="team-card-name">{team.name}</strong>
      </div>
      <p className="team-card-desc">{team.description}</p>
      <div className="team-member-list">
        {team.member_experts.map((expert, index) => (
          <span key={expert.name} className="team-member-chip">
            {index + 1}. {expert.role}
          </span>
        ))}
      </div>
    </button>
  );
}

function avatarText(expert: Expert): string {
  const source = expert.role || expert.name;
  return source.slice(0, 1).toUpperCase();
}
