"""Claude Code-style static card renderer for the agent SSE stream.

Design principles:
- **Static cards, not Live overlays.** Each turn's content prints once and stays;
  we don't redraw history. This matches Claude Code / Codex aesthetics and
  works in any terminal (CI logs, tmux scrollback, etc.).
- **Streaming text accumulates.** ``text`` and ``thinking`` deltas buffer until
  the turn ends, then render as a single Markdown block — avoids ugly partial
  output and keeps the card readable.
- **Reasoning hidden by default (Kimi-style).** A single line ``💭 Thinking
  (Ns, M chars)`` appears unless the user passes ``--show-reasoning expanded``.
- **Tool calls are immediate.** A small panel with arguments prints as soon as
  ``tool_call_start`` arrives; the result panel prints as soon as
  ``tool_result`` arrives. The model "speaks" the tool call.

The renderer consumes events through ``handle(event)`` so it can be driven by
the SSE stream from any source (the CLI uses ``EventStream``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from rich.console import Console, Group
from rich.json import JSON
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .friendly_errors import translate as translate_error


class ReasoningMode(str, Enum):
    HIDDEN = "hidden"        # don't show thinking at all
    COLLAPSED = "collapsed"  # one-line summary (default — Kimi-style)
    EXPANDED = "expanded"    # full text shown as a dimmed panel


@dataclass(slots=True)
class _TurnState:
    turn: int
    text_buffer: list[str] = field(default_factory=list)
    thinking_buffer: list[str] = field(default_factory=list)
    has_tool_calls: bool = False
    elapsed_seconds: float = 0.0


class RichRenderer:
    """Translates SSE events into a sequence of `rich` panels printed in order."""

    def __init__(
        self,
        *,
        agent_name: str,
        console: Console | None = None,
        reasoning: ReasoningMode = ReasoningMode.COLLAPSED,
    ) -> None:
        self._console = console or Console()
        self._agent_name = agent_name
        self._reasoning = reasoning
        self._current: _TurnState | None = None
        self._final_result: str | None = None
        self._error_payload: dict[str, Any] | None = None
        self._usage: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def handle(self, event: dict[str, Any]) -> None:
        kind = event.get("responseType")
        data = event.get("response")
        result_map = event.get("resultMap")

        if kind == "start":
            self._on_start(data)
        elif kind == "step":
            self._on_step(result_map or {})
        elif kind == "thinking":
            self._on_thinking(data)
        elif kind == "text":
            self._on_text(data)
        elif kind == "tool_call_start":
            self._on_tool_call_start(result_map or {})
        elif kind == "tool_result":
            self._on_tool_result(result_map or {})
        elif kind == "step_end":
            self._on_step_end(result_map or {})
        elif kind == "usage":
            self._on_usage(result_map or {})
        elif kind == "result":
            self._on_result(result_map or {})
        elif kind == "error":
            self._on_error(result_map if isinstance(result_map, dict) else (data if isinstance(data, dict) else {"message": str(data)}))

    def finish(self) -> None:
        """Print the closing summary card. Call once after the stream ends."""
        if self._error_payload is not None:
            self._render_error(self._error_payload)
            return

        if self._final_result is None and self._usage is None:
            return

        body: list[Any] = []
        if self._final_result:
            body.append(Markdown(self._final_result))
        if self._usage:
            if body:
                body.append(Text(""))
            body.append(self._build_usage_table(self._usage))

        self._console.print(
            Panel(
                Group(*body),
                title="[bold green]✓ Done[/bold green]",
                border_style="green",
                padding=(1, 2),
            )
        )

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_start(self, data: Any) -> None:
        query = data if isinstance(data, str) else (data.get("query", "") if isinstance(data, dict) else "")
        # The legacy mapping prefixes "开始处理: ". Strip it for nicer display.
        if isinstance(query, str) and query.startswith("开始处理: "):
            query = query[len("开始处理: "):]
        header = Text()
        header.append("🤖 ", style="bold cyan")
        header.append(self._agent_name, style="bold cyan")
        header.append("  ")
        header.append(query, style="white")
        self._console.print()
        self._console.print(Rule(header, style="cyan"))

    def _on_step(self, result_map: dict[str, Any]) -> None:
        turn = int(result_map.get("turn", 0))
        self._current = _TurnState(turn=turn)
        self._console.print()
        self._console.print(Text(f"  ▸ Turn {turn}", style="bold dim"))

    def _on_thinking(self, data: Any) -> None:
        if self._current is None or self._reasoning == ReasoningMode.HIDDEN:
            return
        self._current.thinking_buffer.append(self._coerce_text(data))

    def _on_text(self, data: Any) -> None:
        if self._current is None:
            return
        self._current.text_buffer.append(self._coerce_text(data))

    def _on_tool_call_start(self, result_map: dict[str, Any]) -> None:
        if self._current is not None:
            self._flush_thinking_and_text()
        tool = result_map.get("tool", "?")
        arguments = result_map.get("arguments", {})

        header = Text()
        header.append("🔧 ", style="bold yellow")
        header.append(tool, style="bold yellow")

        try:
            arg_json = json.dumps(arguments, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            arg_json = str(arguments)

        body = (
            Syntax(arg_json, "json", theme="ansi_dark", background_color="default", word_wrap=True)
            if arg_json.strip() and arg_json.strip() != "{}"
            else Text("(no arguments)", style="dim italic")
        )

        self._console.print(
            Panel(
                body,
                title=header,
                title_align="left",
                border_style="yellow",
                padding=(0, 1),
            )
        )

    def _on_tool_result(self, result_map: dict[str, Any]) -> None:
        tool = result_map.get("tool", "?")
        ok = bool(result_map.get("ok", True))
        elapsed = float(result_map.get("elapsed_seconds", 0.0))
        error_type = result_map.get("error_type", "")
        result_text = self._coerce_text(result_map.get("toolResult", ""))

        if ok:
            badge_icon, badge_style, border_style = "✓", "green", "green"
            status_label = "Result"
        else:
            badge_icon, badge_style, border_style = "✗", "red", "red"
            status_label = error_type or "Error"

        title = Text()
        title.append(f"  {badge_icon} ", style=f"bold {badge_style}")
        title.append(f"{tool} · {status_label}", style=f"bold {badge_style}")
        title.append(f"  ({elapsed:.2f}s)", style="dim")

        body: Any
        # If the result looks like JSON, syntax-highlight it.
        stripped = result_text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                json.loads(stripped)
                body = JSON(stripped)
            except (TypeError, ValueError):
                body = Text(result_text)
        else:
            body = Text(result_text)

        self._console.print(
            Panel(
                body,
                title=title,
                title_align="left",
                border_style=border_style,
                padding=(0, 1),
            )
        )

    def _on_step_end(self, result_map: dict[str, Any]) -> None:
        if self._current is None:
            return
        self._current.elapsed_seconds = float(result_map.get("elapsed_seconds", 0.0))
        self._current.has_tool_calls = bool(result_map.get("has_tool_calls", False))

        # Flush any unrendered thinking/text from this turn before closing it.
        self._flush_thinking_and_text()

        # If the turn had no tool calls and produced no text, drop a single
        # dim line so the user knows nothing else is coming.
        self._current = None

    def _on_usage(self, result_map: dict[str, Any]) -> None:
        self._usage = dict(result_map)

    def _on_result(self, result_map: dict[str, Any]) -> None:
        if isinstance(result_map, dict):
            self._final_result = self._coerce_text(
                result_map.get("taskSummary") or result_map.get("result") or ""
            )

    def _on_error(self, payload: dict[str, Any]) -> None:
        self._error_payload = payload

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def _flush_thinking_and_text(self) -> None:
        """Render the current turn's accumulated thinking / text buffers."""
        if self._current is None:
            return

        thinking = "".join(self._current.thinking_buffer)
        if thinking:
            self._render_thinking(thinking)
            self._current.thinking_buffer.clear()

        text = "".join(self._current.text_buffer)
        if text:
            self._render_text(text)
            self._current.text_buffer.clear()

    def _render_thinking(self, content: str) -> None:
        if self._reasoning == ReasoningMode.HIDDEN:
            return

        if self._reasoning == ReasoningMode.COLLAPSED:
            chars = len(content)
            line = Text()
            line.append("    💭 Thinking ", style="dim italic")
            line.append(f"({chars} chars hidden — pass --show-reasoning expanded to view)", style="dim")
            self._console.print(line)
            return

        # Expanded
        self._console.print(
            Panel(
                Markdown(content),
                title="[dim italic]💭 Thinking[/dim italic]",
                title_align="left",
                border_style="dim",
                padding=(0, 1),
            )
        )

    def _render_text(self, content: str) -> None:
        # Final answer rendered as Markdown with a discreet 📝 marker.
        self._console.print(
            Panel(
                Markdown(content),
                title="[bold]📝 Answer[/bold]",
                title_align="left",
                border_style="cyan",
                padding=(1, 2),
            )
        )

    def _render_error(self, payload: dict[str, Any]) -> None:
        friendly = translate_error(payload)
        code = payload.get("code", "error")
        category = payload.get("category", "")

        body = Text()
        body.append(friendly.detail.rstrip() + "\n", style="red")
        if friendly.hint:
            body.append("\n💡 ", style="dim")
            body.append(friendly.hint + "\n", style="dim italic")
        body.append("\n")
        body.append(f"code: {code}", style="dim")
        if category:
            body.append(f"   category: {category}", style="dim")
        if friendly.retryable:
            body.append("   (retryable)", style="dim italic")

        self._console.print(
            Panel(
                body,
                title=f"[bold red]✗ {friendly.headline}[/bold red]",
                border_style="red",
                padding=(1, 2),
            )
        )

    def _build_usage_table(self, usage: dict[str, Any]) -> Table:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim", justify="right")
        table.add_column(style="white")
        table.add_row("Prompt tokens", str(usage.get("prompt_tokens", 0)))
        table.add_row("Completion tokens", str(usage.get("completion_tokens", 0)))
        table.add_row("Total tokens", str(usage.get("total_tokens", 0)))
        table.add_row("Duration", f"{float(usage.get('total_seconds', 0.0)):.2f}s")
        return table

    @staticmethod
    def _coerce_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                return str(value)
        return str(value)
