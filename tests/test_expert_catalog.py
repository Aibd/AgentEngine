"""Tests for the expert directory (presets surfaced with display metadata)."""

from __future__ import annotations

import json
from pathlib import Path

from app.backend.services.expert_catalog import ExpertCatalog


def _write(dir_path: Path, name: str, frontmatter: str, body: str = "Body") -> None:
    (dir_path / f"{name}.md").write_text(
        f"---\n{frontmatter}\n---\n{body}",
        encoding="utf-8",
    )


def _catalog(tmp_path: Path, registry: dict[str, object]) -> ExpertCatalog:
    md = tmp_path / "markdown"
    md.mkdir()
    _write(
        md,
        "stock_expert",
        "name: stock_expert\nrole: 股票研究专家\ndescription: 研报分析\n"
        "author: 文博凯\ncategory: 金融投资\nscenario: 投资分析\n"
        "skills: [股票分析, 估值]\nbadge: ''",
    )
    _write(
        md,
        "writer_expert",
        "name: writer_expert\nrole: 内容创作专家\ndescription: 文案\n"
        "author: 像素君\ncategory: 内容创作\nscenario: 内容创作\nskills: [文案, 叙事]",
    )
    _write(
        md,
        "coach_expert",
        "name: coach_expert\nrole: 创业教练\ndescription: 创业\n"
        "category: OPC·一人公司\nscenario: 小微企业\nskills: 商业模式, 增长\nbadge: Beta",
    )
    # A plain base agent: no ``role`` -> must be excluded from the directory.
    _write(md, "general_chat", "name: general_chat\ndescription: base agent")
    # An expert whose name is NOT registered -> excluded (not runnable).
    _write(
        md,
        "ghost_expert",
        "name: ghost_expert\nrole: 幽灵专家\ncategory: 金融投资",
    )
    return ExpertCatalog(markdown_dir=md, registry=registry)


_REGISTRY: dict[str, object] = {
    "stock_expert": object(),
    "writer_expert": object(),
    "coach_expert": object(),
    "general_chat": object(),
    # ghost_expert intentionally absent
}


def test_list_returns_only_registered_experts_with_role(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, _REGISTRY)
    names = {e.name for e in catalog.list()}
    assert names == {"stock_expert", "writer_expert", "coach_expert"}
    # base agent without role and unregistered expert are both gone
    assert "general_chat" not in names
    assert "ghost_expert" not in names


def test_expert_fields_parsed(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, _REGISTRY)
    stock = next(e for e in catalog.list() if e.name == "stock_expert")
    assert stock.role == "股票研究专家"
    assert stock.author == "文博凯"
    assert stock.category == "金融投资"
    assert stock.scenario == "投资分析"
    assert stock.skills == ["股票分析", "估值"]


def test_skills_accepts_comma_string(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, _REGISTRY)
    coach = next(e for e in catalog.list() if e.name == "coach_expert")
    assert coach.skills == ["商业模式", "增长"]
    assert coach.badge == "Beta"


def test_categories_aggregates_with_all_first(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, _REGISTRY)
    cats = catalog.categories()
    assert cats[0] == "全部"
    assert set(cats) == {"全部", "金融投资", "内容创作", "OPC·一人公司"}


def test_category_filter(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, _REGISTRY)
    assert {e.name for e in catalog.list(category="金融投资")} == {"stock_expert"}


def test_scenario_filter(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, _REGISTRY)
    assert {e.name for e in catalog.list(scenario="内容创作")} == {"writer_expert"}


def test_query_filter(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, _REGISTRY)
    assert {e.name for e in catalog.list(query="股票")} == {"stock_expert"}


def test_scenarios_grouping(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, _REGISTRY)
    groups = {g.scenario: [e.name for e in g.experts] for g in catalog.scenarios()}
    assert groups["投资分析"] == ["stock_expert"]
    assert groups["内容创作"] == ["writer_expert"]
    assert groups["小微企业"] == ["coach_expert"]


def test_get_returns_expert(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, _REGISTRY)
    assert catalog.get("writer_expert") is not None
    assert catalog.get("nope") is None


def test_bundled_experts_load() -> None:
    """The real shipped preset directory exposes experts that are registered."""
    from app.backend.agents import REGISTRY

    repo_root = Path(__file__).resolve().parents[1]
    catalog = ExpertCatalog(
        markdown_dir=repo_root / "app" / "backend" / "agents" / "markdown",
        registry=REGISTRY,
    )
    experts = catalog.list()
    assert len(experts) >= 6
    assert all(expert.name in REGISTRY for expert in experts)
    assert all(expert.role for expert in experts)


# -- expert teams -----------------------------------------------------------


def _team_catalog(tmp_path: Path, registry: dict[str, object]) -> ExpertCatalog:
    catalog = _catalog(tmp_path, registry)  # writes the markdown dir
    teams = tmp_path / "teams.json"
    teams.write_text(
        json.dumps(
            [
                {
                    "id": "invest",
                    "name": "投研团",
                    "description": "研报到交易",
                    "category": "金融投资",
                    "members": ["stock_expert", "writer_expert"],
                },
                {
                    "id": "content",
                    "name": "内容团",
                    "description": "创作接力",
                    "category": "内容创作",
                    "members": ["writer_expert"],
                },
                {
                    # Contains an unregistered member -> must be filtered out.
                    "id": "broken",
                    "name": "坏团",
                    "category": "金融投资",
                    "members": ["stock_expert", "ghost_member"],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return ExpertCatalog(
        markdown_dir=tmp_path / "markdown",
        registry=registry,
        teams_path=teams,
    )


def test_list_teams_resolves_members_and_filters_broken(tmp_path: Path) -> None:
    catalog = _team_catalog(tmp_path, _REGISTRY)
    teams = catalog.list_teams()
    ids = {t.id for t in teams}
    assert ids == {"invest", "content"}  # broken team dropped
    invest = next(t for t in teams if t.id == "invest")
    assert invest.members == ["stock_expert", "writer_expert"]
    assert [e.role for e in invest.member_experts] == ["股票研究专家", "内容创作专家"]


def test_team_categories(tmp_path: Path) -> None:
    catalog = _team_catalog(tmp_path, _REGISTRY)
    cats = catalog.team_categories()
    assert cats[0] == "全部"
    assert set(cats) == {"全部", "金融投资", "内容创作"}


def test_team_category_filter(tmp_path: Path) -> None:
    catalog = _team_catalog(tmp_path, _REGISTRY)
    assert {t.id for t in catalog.list_teams(category="内容创作")} == {"content"}


def test_team_query_filter(tmp_path: Path) -> None:
    catalog = _team_catalog(tmp_path, _REGISTRY)
    assert {t.id for t in catalog.list_teams(query="投研")} == {"invest"}


def test_get_team(tmp_path: Path) -> None:
    catalog = _team_catalog(tmp_path, _REGISTRY)
    assert catalog.get_team("invest") is not None
    assert catalog.get_team("nope") is None


def test_no_teams_path_returns_empty(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, _REGISTRY)  # no teams_path
    assert catalog.list_teams() == []
    assert catalog.team_categories() == ["全部"]
