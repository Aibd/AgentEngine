from __future__ import annotations

import html
from dataclasses import dataclass

from examples.services.reporting.models import ChartSpec


@dataclass(slots=True)
class ChartAsset:
    chart_id: str
    svg: str


_PALETTE = ["#2563eb", "#0f766e", "#f97316", "#7c3aed", "#be123c"]


def render_svg(chart: ChartSpec, *, width: int = 760, height: int = 360) -> str:
    if chart.kind == "bar":
        return _render_bar(chart, width=width, height=height)
    return _render_line(chart, width=width, height=height)


def _render_line(chart: ChartSpec, *, width: int, height: int) -> str:
    margin = {"top": 46, "right": 28, "bottom": 58, "left": 62}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]
    all_values = [float(value or 0) for series in chart.series for value in series.data]
    low, high = _range(all_values)

    def x_at(index: int) -> float:
        if len(chart.x) <= 1:
            return margin["left"] + plot_w / 2
        return margin["left"] + (plot_w * index / (len(chart.x) - 1))

    def y_at(value: float) -> float:
        return margin["top"] + plot_h - ((value - low) / (high - low) * plot_h)

    grid = _grid(width, margin, plot_w, plot_h, low, high)
    paths: list[str] = []
    legends: list[str] = []
    for s_index, series in enumerate(chart.series):
        color = _PALETTE[s_index % len(_PALETTE)]
        points = [
            (x_at(index), y_at(float(value or 0)))
            for index, value in enumerate(series.data[: len(chart.x)])
        ]
        if points:
            d = " ".join(("M" if index == 0 else "L") + f" {x:.2f} {y:.2f}" for index, (x, y) in enumerate(points))
            paths.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')
            for x, y in points:
                paths.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{color}"/>')
        legends.append(_legend_item(series.name, color, margin["left"] + s_index * 150, 24))
    labels = _x_labels(chart.x, margin, plot_w, height)
    return _svg(width, height, chart.title, grid + paths + labels + legends)


def _render_bar(chart: ChartSpec, *, width: int, height: int) -> str:
    margin = {"top": 46, "right": 28, "bottom": 58, "left": 62}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]
    series = chart.series[0] if chart.series else None
    values = [float(value or 0) for value in (series.data if series else [])]
    low, high = _range(values + [0])

    def y_at(value: float) -> float:
        return margin["top"] + plot_h - ((value - low) / (high - low) * plot_h)

    bar_w = plot_w / max(len(values), 1) * 0.58
    bars: list[str] = []
    for index, value in enumerate(values):
        x = margin["left"] + (plot_w * (index + 0.21) / max(len(values), 1))
        y = y_at(max(value, 0))
        baseline = y_at(0)
        height_value = abs(baseline - y)
        bars.append(f'<rect x="{x:.2f}" y="{min(y, baseline):.2f}" width="{bar_w:.2f}" height="{height_value:.2f}" rx="4" fill="{_PALETTE[0]}"/>')
    grid = _grid(width, margin, plot_w, plot_h, low, high)
    labels = _x_labels(chart.x, margin, plot_w, height)
    return _svg(width, height, chart.title, grid + bars + labels)


def _range(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 1.0
    low = min(values)
    high = max(values)
    if low == high:
        high = low + 1
    pad = (high - low) * 0.12
    return low - pad, high + pad


def _grid(width: int, margin: dict[str, int], plot_w: int, plot_h: int, low: float, high: float) -> list[str]:
    out: list[str] = []
    for index in range(5):
        y = margin["top"] + plot_h * index / 4
        value = high - (high - low) * index / 4
        out.append(f'<line x1="{margin["left"]}" y1="{y:.2f}" x2="{width - margin["right"]}" y2="{y:.2f}" stroke="#e5e7eb"/>')
        out.append(f'<text x="{margin["left"] - 10}" y="{y + 4:.2f}" text-anchor="end" font-size="11" fill="#64748b">{value:.1f}</text>')
    out.append(f'<line x1="{margin["left"]}" y1="{margin["top"] + plot_h}" x2="{margin["left"] + plot_w}" y2="{margin["top"] + plot_h}" stroke="#94a3b8"/>')
    out.append(f'<line x1="{margin["left"]}" y1="{margin["top"]}" x2="{margin["left"]}" y2="{margin["top"] + plot_h}" stroke="#94a3b8"/>')
    return out


def _x_labels(labels: list[str], margin: dict[str, int], plot_w: int, height: int) -> list[str]:
    out: list[str] = []
    count = max(len(labels), 1)
    for index, label in enumerate(labels):
        if count == 1:
            x = margin["left"] + plot_w / 2
        else:
            x = margin["left"] + plot_w * index / (count - 1)
        out.append(f'<text x="{x:.2f}" y="{height - 26}" text-anchor="middle" font-size="12" fill="#475569">{html.escape(label)}</text>')
    return out


def _legend_item(name: str, color: str, x: float, y: float) -> str:
    return (
        f'<g><rect x="{x:.2f}" y="{y - 10:.2f}" width="18" height="4" rx="2" fill="{color}"/>'
        f'<text x="{x + 24:.2f}" y="{y:.2f}" font-size="12" fill="#334155">{html.escape(name)}</text></g>'
    )


def _svg(width: int, height: int, title: str, elements: list[str]) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">'
        f'<rect width="100%" height="100%" fill="#ffffff"/>'
        f'<text x="24" y="28" font-size="18" font-weight="700" fill="#0f172a">{html.escape(title)}</text>'
        + "".join(elements)
        + "</svg>"
    )
