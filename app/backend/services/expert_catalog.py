"""Expert directory: the "专家" page, built on top of agent presets.

An *expert* is an :class:`~agentengine.definition.AgentDefinition` with extra,
presentation-only frontmatter — ``role``, ``author``, ``category``,
``scenario``, ``skills``, ``badge``. The same Markdown file is both:

- a runnable agent (loaded into ``app.backend.agents.REGISTRY`` by
  ``preset_loader``, so the chat view can run it via ``agent_name``), and
- a catalog entry (its extended frontmatter is read here for the cards).

Selecting an expert needs no new run endpoint: the frontend just runs
``/api/runs/stream?agent_name=<expert>`` with the existing pipeline.

A preset counts as an *expert* only when it declares a ``role`` — that keeps
the base agents (``general_chat``, ``deep_research``) out of the directory
while requiring no extra flag. Entries whose ``name`` is not in the agent
registry are skipped (they would not be runnable).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Mapping

from agentengine.definition_loader import DEFINITION_FILE_SUFFIX, PRESET_FILE_SUFFIX, _split_frontmatter

logger = logging.getLogger(__name__)

ALL_CATEGORY = "全部"


@dataclass(slots=True)
class ExpertView:
    """A runnable expert plus its display metadata."""

    name: str
    role: str
    description: str
    author: str
    category: str
    scenario: str
    skills: List[str]
    badge: str

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "description": self.description,
            "author": self.author,
            "category": self.category,
            "scenario": self.scenario,
            "skills": list(self.skills),
            "badge": self.badge,
        }


@dataclass(slots=True)
class ScenarioGroup:
    """A featured scenario and the experts grouped under it."""

    scenario: str
    experts: List[ExpertView]

    def snapshot(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "experts": [expert.snapshot() for expert in self.experts],
        }


@dataclass(slots=True)
class ExpertTeam:
    """An ordered group of experts run as a sequential relay pipeline.

    ``members`` is the ordered list of expert agent names; ``member_experts``
    resolves each to a full :class:`ExpertView` for the cards. A team is only
    surfaced when *every* member is a registered, runnable expert.
    """

    id: str
    name: str
    description: str
    category: str
    members: List[str]
    member_experts: List[ExpertView]

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "members": list(self.members),
            "member_experts": [expert.snapshot() for expert in self.member_experts],
        }


class ExpertCatalog:
    """Read expert metadata from the preset Markdown directory."""

    def __init__(
        self,
        *,
        markdown_dir: Path,
        registry: Mapping[str, Any],
        teams_path: Path | None = None,
    ) -> None:
        self._dir = Path(markdown_dir)
        self._registry = registry
        self._teams_path = Path(teams_path) if teams_path is not None else None

    def _load_all(self) -> List[ExpertView]:
        if not self._dir.is_dir():
            logger.warning("expert_dir_missing dir=%s", self._dir)
            return []
        experts: List[ExpertView] = []
        for path in sorted(self._dir.iterdir()):
            if path.suffix != PRESET_FILE_SUFFIX or not path.is_file():
                continue
            view = self._parse(path)
            if view is not None:
                experts.append(view)
        return experts

    def _parse(self, path: Path) -> ExpertView | None:
        try:
            frontmatter, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("expert_parse_failed path=%s", path, exc_info=True)
            return None
        if not frontmatter:
            return None
        role = str(frontmatter.get("role", "")).strip()
        if not role:
            return None  # not an expert — a plain base agent
        name = str(frontmatter.get("name") or path.stem).strip()
        if name not in self._registry:
            logger.debug("expert_not_registered name=%s", name)
            return None
        return ExpertView(
            name=name,
            role=role,
            description=str(frontmatter.get("description", "")).strip(),
            author=str(frontmatter.get("author", "")).strip(),
            category=str(frontmatter.get("category", "")).strip() or ALL_CATEGORY,
            scenario=str(frontmatter.get("scenario", "")).strip(),
            skills=_normalize_skills(frontmatter.get("skills")),
            badge=str(frontmatter.get("badge", "")).strip(),
        )

    def list(
        self,
        *,
        category: str | None = None,
        scenario: str | None = None,
        query: str | None = None,
    ) -> List[ExpertView]:
        wanted_category = (category or "").strip()
        wanted_scenario = (scenario or "").strip()
        wanted_query = (query or "").strip().lower()
        result: List[ExpertView] = []
        for expert in self._load_all():
            if wanted_category and wanted_category != ALL_CATEGORY and expert.category != wanted_category:
                continue
            if wanted_scenario and expert.scenario != wanted_scenario:
                continue
            if wanted_query:
                haystack = " ".join(
                    [expert.role, expert.description, expert.author, " ".join(expert.skills)]
                ).lower()
                if wanted_query not in haystack:
                    continue
            result.append(expert)
        return result

    def categories(self) -> List[str]:
        seen: List[str] = []
        for expert in self._load_all():
            if expert.category and expert.category not in seen:
                seen.append(expert.category)
        return [ALL_CATEGORY, *seen]

    def scenarios(self) -> List[ScenarioGroup]:
        groups: dict[str, List[ExpertView]] = {}
        order: List[str] = []
        for expert in self._load_all():
            if not expert.scenario:
                continue
            if expert.scenario not in groups:
                groups[expert.scenario] = []
                order.append(expert.scenario)
            groups[expert.scenario].append(expert)
        return [ScenarioGroup(scenario=name, experts=groups[name]) for name in order]

    def get(self, name: str) -> ExpertView | None:
        for expert in self._load_all():
            if expert.name == name:
                return expert
        return None

    # -- teams ------------------------------------------------------------

    def _load_teams(self) -> List[ExpertTeam]:
        if self._teams_path is None or not self._teams_path.is_file():
            return []
        try:
            raw = json.loads(self._teams_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("expert_teams_read_error path=%s", self._teams_path, exc_info=True)
            return []
        by_name = {expert.name: expert for expert in self._load_all()}
        teams: List[ExpertTeam] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
                continue
            members = [str(m).strip() for m in (item.get("members") or []) if str(m).strip()]
            if not members:
                continue
            # Only surface a team when every member is a runnable expert.
            resolved = [by_name[m] for m in members if m in by_name]
            if len(resolved) != len(members):
                logger.debug("expert_team_has_unknown_member id=%s", item["id"])
                continue
            teams.append(
                ExpertTeam(
                    id=str(item["id"]),
                    name=str(item["name"]),
                    description=str(item.get("description", "")).strip(),
                    category=str(item.get("category", "")).strip() or ALL_CATEGORY,
                    members=members,
                    member_experts=resolved,
                )
            )
        return teams

    def list_teams(
        self,
        *,
        category: str | None = None,
        query: str | None = None,
    ) -> List[ExpertTeam]:
        wanted_category = (category or "").strip()
        wanted_query = (query or "").strip().lower()
        result: List[ExpertTeam] = []
        for team in self._load_teams():
            if wanted_category and wanted_category != ALL_CATEGORY and team.category != wanted_category:
                continue
            if wanted_query:
                haystack = " ".join(
                    [team.name, team.description, *[e.role for e in team.member_experts]]
                ).lower()
                if wanted_query not in haystack:
                    continue
            result.append(team)
        return result

    def team_categories(self) -> List[str]:
        seen: List[str] = []
        for team in self._load_teams():
            if team.category and team.category not in seen:
                seen.append(team.category)
        return [ALL_CATEGORY, *seen]

    def get_team(self, team_id: str) -> ExpertTeam | None:
        for team in self._load_teams():
            if team.id == team_id:
                return team
        return None


def _normalize_skills(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(part).strip() for part in raw if str(part).strip()]
    return []


__all__ = ["ALL_CATEGORY", "ExpertCatalog", "ExpertTeam", "ExpertView", "ScenarioGroup"]
