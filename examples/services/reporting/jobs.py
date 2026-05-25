from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentengine.llm.env import create_llm_from_env
from agentengine.memory.message import Message
from examples.services.reporting.charts import render_svg
from examples.services.reporting.file_store import ReportFileRecord
from examples.services.reporting.metrics import analyze_first_sheet
from examples.services.reporting.models import ChartSpec, ChartSeries, ReportBlock, ReportData, empty_report


logger = logging.getLogger(__name__)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ReportJob:
    id: str
    conversation_id: str
    title: str
    intent: str
    ir: ReportData
    skill: str = "data_analysis"
    file_ids: list[str] = field(default_factory=list)
    file_briefs: list[dict[str, Any]] = field(default_factory=list)
    chart_assets: dict[str, str] = field(default_factory=dict)
    status: str = "created"
    exports: dict[str, str] = field(default_factory=dict)
    error: str = ""
    created_at: str = field(default_factory=_utc_iso)
    finished_at: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "title": self.title,
            "intent": self.intent,
            "skill": self.skill,
            "file_ids": self.file_ids,
            "file_briefs": self.file_briefs,
            "chart_ids": sorted(self.chart_assets),
            "status": self.status,
            "ir": self.ir.model_dump(mode="json"),
            "exports": self.exports,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class ReportJobStore:
    """Process-local report store for the demo service.

    The production version should swap this for a database-backed repository,
    but keeping this store small makes the artifact protocol testable first.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, ReportJob] = {}

    def create(
        self,
        *,
        conversation_id: str,
        title: str,
        intent: str,
        skill: str = "data_analysis",
        files: list[ReportFileRecord] | None = None,
    ) -> ReportJob:
        report_id = f"report_{uuid.uuid4().hex[:12]}"
        file_records = files or []
        job = ReportJob(
            id=report_id,
            conversation_id=conversation_id,
            title=title,
            intent=intent,
            skill=skill,
            ir=empty_report(report_id, title=title, period=_period_from_files(file_records)),
            file_ids=[record.id for record in file_records],
            file_briefs=[_file_brief(record) for record in file_records],
        )
        self._jobs[report_id] = job
        return job

    def get(self, report_id: str) -> ReportJob | None:
        return self._jobs.get(report_id)


async def stream_report_artifact(
    *,
    job: ReportJob,
    request_id: str,
) -> AsyncIterator[dict[str, Any]]:
    if job.status == "ready":
        if not job.exports:
            job.exports = _export_urls(job.id)
        yield _frame("start", job, request_id, {"query": job.intent or job.title})
        yield _frame("step", job, request_id, {"turn": 1})
        yield _frame(
            "text",
            job,
            request_id,
            {"delta": "报告已经生成完成，正在恢复右侧预览和导出入口。\n"},
        )
        yield _frame(
            "artifact_start",
            job,
            request_id,
            {"title": job.title, "status": job.status},
        )
        for block in job.ir.sections:
            if block.type == "chart" and block.chart_id in job.chart_assets:
                yield _frame(
                    "artifact_chart_ready",
                    job,
                    request_id,
                    {
                        "chart_id": block.chart_id,
                        "preview_url": f"/api/reports/{job.id}/charts/{block.chart_id}.svg",
                        "title": block.chart.title if block.chart else block.text or "",
                    },
                )
            yield _frame("artifact_block_added", job, request_id, block.model_dump(mode="json"))
        yield _frame("step_end", job, request_id, {"turn": 1, "has_tool_calls": False, "elapsed_ms": 0})
        yield _frame("artifact_ready", job, request_id, {"ir": job.ir.model_dump(mode="json")})
        yield _frame("artifact_export_ready", job, request_id, {"exports": job.exports})
        return

    job.status = "running"
    started_at = time.perf_counter()
    yield _frame("start", job, request_id, {"query": job.intent or job.title})
    yield _frame("step", job, request_id, {"turn": 1})
    yield _frame(
        "thinking",
        job,
        request_id,
        {
            "delta": (
                "先识别用户选择的数据分析技能和输入要求，再把本会话上传的文件解析结果整理成模型上下文；"
                "报告结构由模型按提示词决定，ReportData 只用于右侧预览和导出。"
            )
        },
    )
    yield _frame(
        "text",
        job,
        request_id,
        {"delta": "开始生成财务分析报告。\n\n"},
    )
    file_context_call_id = f"call_{uuid.uuid4().hex[:8]}"
    yield _frame(
        "tool_call_start",
        job,
        request_id,
        {
            "turn": 1,
            "tool_call_id": file_context_call_id,
            "tool": "prepare_uploaded_file_context",
            "arguments": {
                "file_ids": job.file_ids,
                "file_count": len(job.file_briefs),
                "skill": job.skill,
            },
        },
    )
    yield _frame(
        "tool_result",
        job,
        request_id,
        {
            "turn": 1,
            "tool_call_id": file_context_call_id,
            "tool": "prepare_uploaded_file_context",
            "ok": True,
            "elapsed_ms": 0,
            "result": {
                "files": [
                    {
                        "filename": file.get("filename", ""),
                        "sheets": len(file.get("sheets", [])),
                        "has_text_preview": bool(str(file.get("text_preview", "")).strip()),
                    }
                    for file in job.file_briefs
                ],
            },
        },
    )
    yield _frame(
        "text",
        job,
        request_id,
        {
            "delta": (
                f"已整理 {len(job.file_briefs)} 个上传文件的表格/文本预览，"
                "接下来让模型按你的提示词生成报告内容。\n\n"
            )
        },
    )
    yield _frame(
        "artifact_start",
        job,
        request_id,
        {
            "type": "financial_report",
            "title": job.title,
            "status": job.status,
        },
    )

    llm_call_id = f"call_{uuid.uuid4().hex[:8]}"
    yield _frame(
        "tool_call_start",
        job,
        request_id,
        {
            "turn": 1,
            "tool_call_id": llm_call_id,
            "tool": "generate_report_blocks",
            "arguments": {
                "skill": job.skill,
                "intent": job.intent,
                "title": job.title,
                "file_count": len(job.file_briefs),
            },
        },
    )
    sections = await _agent_sections(job)
    used_llm = sections is not None
    if sections is None:
        sections = _demo_sections(job)
    yield _frame(
        "tool_result",
        job,
        request_id,
        {
            "turn": 1,
            "tool_call_id": llm_call_id,
            "tool": "generate_report_blocks",
            "ok": True,
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            "result": {
                "mode": "llm" if used_llm else "fallback",
                "section_count": len(sections),
                "block_count": sum(len(blocks) for _, blocks in sections),
            },
        },
    )
    yield _frame(
        "text",
        job,
        request_id,
        {
            "delta": (
                "模型已返回报告结构，正在逐段输出到右侧报告预览。\n\n"
                if used_llm
                else "当前未拿到可用模型输出，已切换到本地兜底报告结构并继续输出。\n\n"
            )
        },
    )

    for section_name, blocks in sections:
        yield _frame("artifact_section_started", job, request_id, {"name": section_name})
        yield _frame(
            "text",
            job,
            request_id,
            {"delta": f"输出章节：{section_name}\n"},
        )
        for block in blocks:
            if block.type == "chart" and block.chart is not None:
                chart_id = block.chart_id or f"chart_{uuid.uuid4().hex[:8]}"
                block.chart_id = chart_id
                job.chart_assets[chart_id] = render_svg(block.chart)
                yield _frame(
                    "artifact_chart_ready",
                    job,
                    request_id,
                    {
                        "chart_id": chart_id,
                        "preview_url": f"/api/reports/{job.id}/charts/{chart_id}.svg",
                        "title": block.chart.title,
                    },
                )
            job.ir.append_block(block)
            yield _frame("artifact_block_added", job, request_id, block.model_dump(mode="json"))

    job.status = "ready"
    job.finished_at = _utc_iso()
    job.exports = _export_urls(job.id)
    export_call_id = f"call_{uuid.uuid4().hex[:8]}"
    yield _frame(
        "tool_call_start",
        job,
        request_id,
        {
            "turn": 1,
            "tool_call_id": export_call_id,
            "tool": "prepare_report_exports",
            "arguments": {"formats": sorted(job.exports)},
        },
    )
    yield _frame(
        "tool_result",
        job,
        request_id,
        {
            "turn": 1,
            "tool_call_id": export_call_id,
            "tool": "prepare_report_exports",
            "ok": True,
            "elapsed_ms": 0,
            "result": job.exports,
        },
    )
    yield _frame(
        "text",
        job,
        request_id,
        {"delta": "\n报告内容已完成，Word / PDF / HTML / Markdown 导出入口已准备好。"},
    )
    yield _frame(
        "step_end",
        job,
        request_id,
        {
            "turn": 1,
            "has_tool_calls": True,
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
        },
    )
    yield _frame("artifact_ready", job, request_id, {"ir": job.ir.model_dump(mode="json")})
    yield _frame("artifact_export_ready", job, request_id, {"exports": job.exports})


async def _agent_sections(job: ReportJob) -> list[tuple[str, list[ReportBlock]]] | None:
    """Ask the configured LLM to produce report blocks from the selected skill.

    ReportData remains a rendering/export protocol. The content path is model
    first: skill prompt + user intent + parsed file context. If the app is run
    without LLM_* environment variables, or the model returns invalid JSON, the
    deterministic demo generator remains a local fallback.
    """

    llm = create_llm_from_env(required=False)
    if llm is None:
        return None

    try:
        response = await llm.chat(
            _build_agent_messages(job),
            temperature=0.25,
            max_tokens=4096,
        )
        blocks = parse_model_report_blocks(response.content)
    except Exception as exc:  # pragma: no cover - network/provider failures are environment-specific.
        logger.warning("report_agent_generation_failed report_id=%s error=%s", job.id, exc)
        return None
    finally:
        close = getattr(llm, "close", None)
        if close is not None:
            with suppress(Exception):
                await close()

    if not blocks:
        return None
    return [("AI generated report", blocks)]


def _build_agent_messages(job: ReportJob) -> list[Message]:
    system_prompt = _skill_prompt(job.skill)
    file_context = _format_file_context(job.file_briefs)
    user_prompt = (
        f"User request:\n{job.intent or job.title}\n\n"
        f"Report title:\n{job.title}\n\n"
        f"Uploaded file context:\n{file_context or 'No uploaded file context was provided.'}\n\n"
        "Return only JSON that matches this shape:\n"
        "{\n"
        '  "blocks": [\n'
        '    {"type":"heading","level":1,"text":"..."},\n'
        '    {"type":"paragraph","text":"..."},\n'
        '    {"type":"kpi","kpis":[{"label":"...","value":"...","delta":"...","trend":"up|down|flat"}]},\n'
        '    {"type":"table","headers":["..."],"rows":[["..."]]},\n'
        '    {"type":"chart","text":"...","chart":{"kind":"bar|line|pie|area|stacked_bar|grouped_bar|waterfall","title":"...","x":["..."],"series":[{"name":"...","data":[1,2,3]}],"y_format":"currency|percent|number"}},\n'
        '    {"type":"callout","text":"..."}\n'
        "  ]\n"
        "}\n"
        "Do not include markdown fences or explanatory text outside JSON."
    )
    return [Message.system(system_prompt), Message.user(user_prompt)]


def _skill_prompt(skill: str) -> str:
    if skill == "data_analysis":
        return (
            "You are an agentic financial/data analysis report writer. The report content must be driven by the "
            "user request, not by a fixed template. Use the uploaded file context as source evidence. If a number "
            "is not present in the context, say it is unavailable instead of inventing it. Choose the sections, "
            "tables, KPIs, and charts that best satisfy the user's prompt. Write professional Chinese by default "
            "unless the user clearly asks for another language. Keep chart data grounded in the provided previews."
        )
    return (
        "You are an agentic report writer. Follow the user's prompt closely, use uploaded file context when present, "
        "and output a polished structured report as JSON blocks."
    )


def _format_file_context(file_briefs: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for index, file in enumerate(file_briefs, start=1):
        lines = [
            f"File {index}: {file.get('filename', '')}",
            f"file_id={file.get('file_id', '')}",
            f"size_bytes={file.get('size_bytes', '')}",
        ]
        page_count = int(file.get("page_count") or 0)
        if page_count:
            lines.append(f"page_count={page_count}")
        text_preview = str(file.get("text_preview", "")).strip()
        if text_preview:
            lines.append("text_preview:")
            lines.append(text_preview[:5000])
        for sheet in file.get("sheets", [])[:5]:
            rows = sheet.get("rows", []) if isinstance(sheet, dict) else []
            headers = sheet.get("headers", []) if isinstance(sheet, dict) else []
            lines.append(
                "sheet="
                f"{sheet.get('name', '')}; rows={sheet.get('row_count', 0)}; "
                f"columns={sheet.get('column_count', 0)}; headers={headers}"
            )
            for row in rows[:12]:
                lines.append("row=" + json.dumps(row, ensure_ascii=False))
        warnings = file.get("warnings", [])
        if warnings:
            lines.append("warnings=" + json.dumps(warnings, ensure_ascii=False))
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def parse_model_report_blocks(content: str) -> list[ReportBlock]:
    data = _loads_model_json(content)
    raw_blocks: Any
    if isinstance(data, dict):
        if "blocks" in data:
            raw_blocks = data["blocks"]
        elif "sections" in data:
            raw_blocks = data["sections"]
        else:
            raise ValueError("model report JSON must contain a blocks array")
    else:
        raw_blocks = data
    if not isinstance(raw_blocks, list):
        raise ValueError("model report JSON must contain a blocks array")

    blocks: list[ReportBlock] = []
    for raw in raw_blocks:
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_model_block(raw)
        blocks.append(ReportBlock.model_validate(normalized))
    return blocks


def _loads_model_json(content: str) -> Any:
    text = content.strip()
    if not text:
        raise ValueError("empty model response")
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start_candidates = [index for index in (text.find("{"), text.find("[")) if index >= 0]
        if not start_candidates:
            raise
        start = min(start_candidates)
        end = max(text.rfind("}"), text.rfind("]"))
        if end <= start:
            raise
        return json.loads(text[start : end + 1])


def _normalize_model_block(raw: dict[str, Any]) -> dict[str, Any]:
    block = dict(raw)
    block_type = block.get("type")
    if block_type == "chart" and block.get("chart") and not block.get("chart_id"):
        block["chart_id"] = f"chart_{uuid.uuid4().hex[:8]}"
    if block_type == "heading" and block.get("level") is None:
        block["level"] = 2
    if block_type == "table":
        block["headers"] = [str(item) for item in block.get("headers") or []]
        block["rows"] = [
            ["" if cell is None else cell for cell in row]
            for row in block.get("rows") or []
            if isinstance(row, list)
        ]
    return block


def _frame(
    event: str,
    job: ReportJob,
    request_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "artifact_id": job.id,
        "report_id": job.id,
        "conversation_id": job.conversation_id,
        "request_id": request_id,
        **data,
    }
    return {"event": event, "data": payload}


def _export_urls(report_id: str) -> dict[str, str]:
    return {
        "docx": f"/api/reports/{report_id}/exports/docx",
        "pdf": f"/api/reports/{report_id}/exports/pdf",
        "html": f"/api/reports/{report_id}/exports/html",
        "md": f"/api/reports/{report_id}/exports/md",
    }


def _demo_sections(job: ReportJob) -> list[tuple[str, list[ReportBlock]]]:
    if job.file_briefs:
        return _file_based_sections(job)
    return [
        (
            "执行摘要",
            [
                ReportBlock(type="heading", level=1, text=job.title),
                ReportBlock(
                    type="paragraph",
                    text=(
                        "本报告流展示了 AgentEngine artifact 协议的第一阶段能力："
                        "后端持续发送结构化报告块，前端右侧面板实时渲染同一份 IR。"
                    ),
                ),
                ReportBlock(
                    type="kpi",
                    kpis=[
                        {"label": "营业收入", "value": "8.05 亿", "delta": "+15.0%", "trend": "up"},
                        {"label": "净利润率", "value": "29.4%", "delta": "+2.1pct", "trend": "up"},
                        {"label": "经营现金流", "value": "2.42 亿", "delta": "+8.6%", "trend": "up"},
                    ],
                ),
            ],
        ),
        (
            "收入趋势",
            [
                ReportBlock(type="heading", level=2, text="收入趋势与盈利质量"),
                ReportBlock(
                    type="chart",
                    chart_id="chart_revenue_demo",
                    text="示例收入与利润趋势图",
                    chart=ChartSpec(
                        kind="line",
                        title="收入与净利润趋势",
                        x=["2023Q1", "2023Q2", "2023Q3", "2024Q1"],
                        y_format="currency",
                        series=[
                            ChartSeries(name="营业收入", data=[6.98, 7.46, 7.67, 8.05]),
                            ChartSeries(name="净利润", data=[1.51, 1.84, 1.97, 2.37]),
                        ],
                    ),
                ),
                ReportBlock(
                    type="paragraph",
                    text=(
                        "示例数据表明收入保持温和增长，净利润增速更快，说明费用控制和规模效应"
                        "正在改善盈利质量。后续接入解析器后，这些数字会全部来自上传文件。"
                    ),
                ),
            ],
        ),
        (
            "关键指标",
            [
                ReportBlock(type="heading", level=2, text="关键财务指标"),
                ReportBlock(
                    type="table",
                    headers=["指标", "2023Q1", "2024Q1", "变化"],
                    rows=[
                        ["营业收入", "6.98 亿", "8.05 亿", "+15.0%"],
                        ["净利润", "1.51 亿", "2.37 亿", "+57.0%"],
                        ["净利率", "21.6%", "29.4%", "+7.8pct"],
                    ],
                ),
                ReportBlock(
                    type="callout",
                    text="当前阶段是协议和渲染骨架，下一步会接入真实文件解析、指标计算和图表图片导出。",
                ),
            ],
        ),
    ]


def _file_based_sections(job: ReportJob) -> list[tuple[str, list[ReportBlock]]]:
    first_file = job.file_briefs[0]
    first_sheet = first_file.get("sheets", [{}])[0] if first_file.get("sheets") else {}
    headers = [str(item) for item in first_sheet.get("headers", [])]
    rows = first_sheet.get("rows", [])
    row_count = int(first_sheet.get("row_count") or 0)
    column_count = int(first_sheet.get("column_count") or 0)
    metrics = analyze_first_sheet(job.file_briefs)
    file_table_rows = [
        [
            str(file["filename"]),
            str(len(file.get("sheets", []))),
            ", ".join(str(sheet.get("name", "")) for sheet in file.get("sheets", [])[:3]),
        ]
        for file in job.file_briefs
    ]
    preview_rows = [
        [str(cell) for cell in row]
        for row in rows[:8]
        if isinstance(row, list)
    ]
    metric_kpis = metrics.kpis or [
        {"label": "上传文件", "value": str(len(job.file_briefs)), "delta": "已解析", "trend": "flat"},
        {"label": "预览行数", "value": str(row_count), "delta": "数据行", "trend": "flat"},
        {"label": "字段数量", "value": str(column_count), "delta": "列", "trend": "flat"},
    ]
    sections = [
        (
            "文件概览",
            [
                ReportBlock(type="heading", level=1, text=job.title),
                ReportBlock(
                    type="paragraph",
                    text=(
                        "系统已接入真实上传文件链路。本阶段先完成文件保存、CSV/XLSX 预览解析、"
                        "报告 IR 生成和右侧 Artifact 实时渲染；后续会把指标计算和 LLM 分析接入这些结构化数据。"
                    ),
                ),
                ReportBlock(type="kpi", kpis=metric_kpis),
                ReportBlock(
                    type="table",
                    headers=["文件名", "Sheet 数", "Sheet 预览"],
                    rows=file_table_rows,
                ),
            ],
        ),
        (
            "指标分析",
            [
                ReportBlock(type="heading", level=2, text="核心指标识别"),
                *(
                    [
                        ReportBlock(
                            type="chart",
                            chart_id="chart_core_metrics",
                            text="核心财务指标趋势",
                            chart=metrics.chart,
                        )
                    ]
                    if metrics.chart is not None
                    else []
                ),
                *[
                    ReportBlock(type="paragraph", text=finding)
                    for finding in metrics.findings
                ],
                *[
                    ReportBlock(type="callout", text=f"数据提示：{warning}")
                    for warning in metrics.warnings
                ],
            ],
        ),
        *(
            [
                (
                    "附件文本摘要",
                    [
                        ReportBlock(type="heading", level=2, text="附件文本摘要"),
                        *[
                            ReportBlock(
                                type="paragraph",
                                text=f"{file.get('filename')}: {str(file.get('text_preview', ''))[:1200]}",
                            )
                            for file in job.file_briefs
                            if str(file.get("text_preview", "")).strip()
                        ],
                    ],
                )
            ]
            if any(str(file.get("text_preview", "")).strip() for file in job.file_briefs)
            else []
        ),
        (
            "数据预览",
            [
                ReportBlock(type="heading", level=2, text=f"数据预览：{first_sheet.get('name', 'Sheet')}"),
                ReportBlock(
                    type="table",
                    headers=headers,
                    rows=preview_rows,
                ),
                ReportBlock(
                    type="paragraph",
                    text=(
                        "这张表来自上传文件的首个可读工作表。当前预览最多展示前 20 行、30 列，"
                        "用于后续字段映射、指标识别和图表生成。"
                    ),
                ),
            ],
        ),
        (
            "下一步分析",
            [
                ReportBlock(type="heading", level=2, text="待接入的财务分析能力"),
                ReportBlock(
                    type="callout",
                    text=(
                        "下一步会基于这些解析结果增加字段映射、收入/利润/现金流指标计算、"
                        "图表图片导出，以及从同一份 ReportData 生成 HTML/PDF/Word。"
                    ),
                ),
            ],
        ),
    ]
    return [(name, blocks) for name, blocks in sections if blocks]


def _period_from_files(files: list[ReportFileRecord]) -> str:
    if not files:
        return "Demo"
    return "Uploaded data"


def _file_brief(record: ReportFileRecord) -> dict[str, Any]:
    parsed = record.parsed.model_dump(mode="json")
    return {
        "file_id": record.id,
        "filename": record.filename,
        "size_bytes": record.size_bytes,
        "sheets": parsed.get("sheets", []),
        "text_preview": parsed.get("text_preview", ""),
        "page_count": parsed.get("page_count", 0),
        "warnings": parsed.get("warnings", []),
    }
