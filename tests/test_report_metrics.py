from __future__ import annotations

from app.backend.services.reporting.metrics import analyze_first_sheet


def test_analyze_first_sheet_detects_core_financial_metrics() -> None:
    metrics = analyze_first_sheet([
        {
            "filename": "finance.csv",
            "sheets": [
                {
                    "name": "Sheet1",
                    "headers": ["period", "revenue", "net_income"],
                    "rows": [
                        ["2023Q1", "698000000", "151000000"],
                        ["2024Q1", "805000000", "237000000"],
                    ],
                    "row_count": 2,
                    "column_count": 3,
                }
            ],
        }
    ])

    assert [kpi.label for kpi in metrics.kpis] == ["营业收入", "净利润"]
    assert metrics.kpis[0].value == "8.05 亿"
    assert metrics.kpis[0].delta == "+15.3%"
    assert metrics.chart is not None
    assert metrics.chart.title == "核心财务指标趋势"
    assert metrics.findings


def test_analyze_first_sheet_warns_when_metrics_are_missing() -> None:
    metrics = analyze_first_sheet([
        {
            "filename": "ops.csv",
            "sheets": [
                {
                    "name": "Sheet1",
                    "headers": ["name", "note"],
                    "rows": [["a", "b"]],
                }
            ],
        }
    ])

    assert metrics.kpis == []
    assert "no known financial metric columns were detected" in metrics.warnings
