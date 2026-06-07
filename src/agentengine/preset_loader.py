"""Load :class:`AgentPreset` definitions from Markdown + YAML frontmatter.

This mirrors the Claude Code subagent format: a ``.md`` file whose YAML
frontmatter carries the agent's metadata and whose body is the system
instructions. It lets host applications declare agents as data files instead
of Python code.

Example ``deep_research.md``::

    ---
    name: deep_research
    description: Deep research agent with model-native planning.
    tools: [read_file, Skill]
    ---
    You are a deep research assistant. Break the task into clear questions,
    use tools to gather evidence, and then synthesize a grounded answer.

Frontmatter keys map onto :class:`AgentPreset` fields:

==================== =========================================================
Key                  Meaning
==================== =========================================================
``name``             Agent name (defaults to the file stem).
``description``      One-line summary.
``instructions``     System instructions. If omitted, the Markdown *body* is
                     used instead — the idiomatic form.
``tools``            List of builtin tool names to attach. Compiled into a
                     ``setup`` hook that registers each tool by name.
``max_messages``     Forwarded to :class:`AgentPreset`.
``auto_compact_tokens``      Forwarded to :class:`AgentPreset`.
``compaction_keep_recent``   Forwarded to :class:`AgentPreset`.
==================== =========================================================

Hooks that cannot be expressed as data (a custom ``compactor`` or a bespoke
``setup`` that does more than attach named tools) still require code; callers
can post-process the returned preset with :func:`dataclasses.replace`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from agentengine.preset import AgentPreset
from agentengine.run_config import SetupHook

if TYPE_CHECKING:
    from agentengine.base.context import AgentContext

logger = logging.getLogger(__name__)

PRESET_FILE_SUFFIX = ".md"

# Frontmatter keys consumed directly as AgentPreset scalar fields.
_SCALAR_FIELDS = ("max_messages", "auto_compact_tokens", "compaction_keep_recent")


def load_preset(path: str | Path) -> AgentPreset:
    """Load a single Markdown preset file into an :class:`AgentPreset`.

    Raises:
        FileNotFoundError: the path does not exist.
        ValueError: the file has no YAML frontmatter or invalid YAML.
    """
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(content)
    if frontmatter is None:
        raise ValueError(f"Preset file has no YAML frontmatter: {file_path}")
    return _build_preset(frontmatter, body, default_name=file_path.stem)


def load_presets(directory: str | Path) -> dict[str, AgentPreset]:
    """Load every ``*.md`` preset in *directory*, keyed by preset name.

    The scan is single-layer (no recursion) and skips dotfiles. Files without
    valid frontmatter are logged and skipped rather than aborting the scan, so
    one malformed file does not take down the whole registry.
    """
    root = Path(directory)
    presets: dict[str, AgentPreset] = {}
    if not root.is_dir():
        logger.warning("preset_dir_missing dir=%s", root)
        return presets

    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") or entry.suffix != PRESET_FILE_SUFFIX:
            continue
        if not entry.is_file():
            continue
        try:
            preset = load_preset(entry)
        except (OSError, ValueError):
            logger.warning("preset_load_failed path=%s", entry, exc_info=True)
            continue
        if preset.name in presets:
            logger.warning("preset_name_collision name=%s path=%s", preset.name, entry)
            continue
        presets[preset.name] = preset
        logger.debug("preset_loaded name=%s path=%s", preset.name, entry)

    logger.info("preset_scan_complete count=%d dir=%s", len(presets), root)
    return presets


def _build_preset(
    frontmatter: dict[str, Any],
    body: str,
    *,
    default_name: str,
) -> AgentPreset:
    name = str(frontmatter.get("name") or default_name).strip()
    if not name:
        raise ValueError("Preset 'name' must not be empty")

    instructions = frontmatter.get("instructions")
    if instructions is None:
        instructions = body
    instructions = str(instructions).strip()

    kwargs: dict[str, Any] = {
        "name": name,
        "description": str(frontmatter.get("description", "")).strip(),
        "instructions": instructions,
    }
    for field_name in _SCALAR_FIELDS:
        if field_name in frontmatter and frontmatter[field_name] is not None:
            kwargs[field_name] = int(frontmatter[field_name])

    tools = _normalize_tools(frontmatter.get("tools"))
    if tools:
        kwargs["setup"] = _make_tool_setup(tools)
        kwargs["extras"] = {"tools": tools}

    return AgentPreset(**kwargs)


def _normalize_tools(raw: Any) -> tuple[str, ...]:
    """Accept ``tools`` as a YAML list or a comma-separated string."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        items = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        items = [str(part).strip() for part in raw]
    else:
        raise ValueError(f"'tools' must be a list or string, got {type(raw).__name__}")
    return tuple(name for name in items if name)


def _make_tool_setup(tool_names: tuple[str, ...]) -> SetupHook:
    """Build a setup hook that attaches the named builtin tools by name.

    Tools already present on the context are left untouched, matching the
    idempotent guard hand-written setups used. Construction is deferred to
    call time so importing a preset never forces the tool modules to load.
    """

    async def _setup(context: "AgentContext") -> None:
        # Imported lazily to avoid a circular import (builtin tools depend on
        # several runtime modules that may import preset machinery).
        from agentengine.tools.builtin import build_default_tools_for_context

        collection = context.tool_collection
        missing = [name for name in tool_names if collection.get(name) is None]
        if not missing:
            return
        for tool in build_default_tools_for_context(context, include=missing):
            if collection.get(tool.name) is None:
                collection.add(tool)

    return _setup


def _split_frontmatter(content: str) -> tuple[dict[str, Any] | None, str]:
    """Split YAML frontmatter (``--- ... ---``) from the body.

    Returns ``(None, content)`` when no frontmatter delimiter is found so
    callers can tell "no frontmatter" apart from "empty frontmatter".
    """
    match = re.match(
        r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n)?(.*)", content, re.DOTALL
    )
    if not match:
        return None, content
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML frontmatter: {exc}") from exc
    if not isinstance(fm, dict):
        raise ValueError("YAML frontmatter must be a mapping")
    return fm, match.group(2)


__all__ = ["load_preset", "load_presets", "PRESET_FILE_SUFFIX"]
