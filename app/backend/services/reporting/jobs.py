from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agentengine.llm.env import create_llm_from_env
from agentengine.memory.message import Message
from app.backend.services.reporting.file_store import ReportFileRecord

if TYPE_CHECKING:
    from app.backend.services.reporting.db import ReportMetadataDB


logger = logging.getLogger(__name__)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReportJob:
    id: str
    conversation_id: str
    title: str
    intent: str
    public_conversation_id: str = ""
    skill: str = "data_analysis"
    file_ids: list[str] = field(default_factory=list)
    file_briefs: list[dict[str, Any]] = field(default_factory=list)
    html: str = ""
    chart_assets: dict[str, str] = field(default_factory=dict)
    status: str = "created"
    exports: dict[str, str] = field(default_factory=dict)
    error: str = ""
    created_at: str = field(default_factory=_utc_iso)
    finished_at: str = ""
    events: list[dict[str, Any]] = field(default_factory=list, repr=False)
    event_signal: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    generation_task: asyncio.Task[None] | None = field(default=None, repr=False)
    # When the user retries / continues from a previously failed or
    # interrupted report, we stash the half-baked HTML here so the next
    # generation can resume from the breakpoint instead of starting over.
    resume_from_html: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.public_conversation_id or self.conversation_id,
            "title": self.title,
            "intent": self.intent,
            "skill": self.skill,
            "file_ids": self.file_ids,
            "file_briefs": self.file_briefs,
            "chart_ids": sorted(self.chart_assets),
            "status": self.status,
            "html": self.html,
            "html_length": len(self.html),
            "exports": self.exports,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class ReportJobStore:
    """Report store backed by in-memory cache + SQLite persistence.

    Reports are cached in ``_jobs`` for fast access during active
    generation. Once a report reaches ``ready`` or ``failed`` status,
    its HTML is written to disk and metadata is upserted into the
    ``reports`` table of ``ReportMetadataDB`` so it survives process
    restarts.

    When a report is requested via ``aload()`` and not found in
    memory, the store falls back to the DB + disk, reconstructing
    a ReportJob from the persisted data.
    """

    def __init__(
        self,
        db: "ReportMetadataDB",
        reports_dir: Path,
    ) -> None:
        self._jobs: dict[str, ReportJob] = {}
        self._db = db
        self._reports_dir = reports_dir
        reports_dir.mkdir(parents=True, exist_ok=True)

    # -- Creation ------------------------------------------------------

    def create(
        self,
        *,
        conversation_id: str,
        public_conversation_id: str = "",
        title: str,
        intent: str,
        skill: str = "data_analysis",
        files: list[ReportFileRecord] | None = None,
        resume_from_html: str = "",
    ) -> ReportJob:
        report_id = f"report_{uuid.uuid4().hex[:12]}"
        file_records = files or []
        job = ReportJob(
            id=report_id,
            conversation_id=conversation_id,
            public_conversation_id=public_conversation_id,
            title=title,
            intent=intent,
            skill=skill,
            file_ids=[record.id for record in file_records],
            file_briefs=[_file_brief(record) for record in file_records],
            resume_from_html=resume_from_html,
        )
        self._jobs[report_id] = job
        return job

    async def acreate(
        self,
        *,
        conversation_id: str,
        public_conversation_id: str = "",
        title: str,
        intent: str,
        skill: str = "data_analysis",
        files: list[ReportFileRecord] | None = None,
        resume_from_html: str = "",
    ) -> ReportJob:
        """Create a job AND write its metadata row to the DB.

        The job is still in ``created`` status — HTML will be persisted
        later when generation finishes.
        """
        job = self.create(
            conversation_id=conversation_id,
            public_conversation_id=public_conversation_id,
            title=title,
            intent=intent,
            skill=skill,
            files=files,
            resume_from_html=resume_from_html,
        )
        await self._db.insert_report(
            report_id=job.id,
            conversation_id=job.conversation_id,
            title=job.title,
            intent=job.intent,
            skill=job.skill,
        )
        return job

    # -- Retrieval -----------------------------------------------------

    def get(self, report_id: str) -> ReportJob | None:
        return self._jobs.get(report_id)

    async def aload(self, report_id: str) -> ReportJob | None:
        """Load a report from memory cache or, on miss, from DB + disk.

        When loaded from persisted storage the job is placed back into
        the memory cache so subsequent calls are instant.
        """
        cached = self._jobs.get(report_id)
        if cached is not None:
            return cached

        row = await self._db.get_report(report_id)
        if row is None:
            return None

        html = ""
        if row.html_file_path and row.html_size > 0:
            html_path = self._reports_dir / row.html_file_path
            try:
                html = html_path.read_text(encoding="utf-8")
            except OSError:
                logger.warning("report_html_file_missing report_id=%s path=%s", report_id, html_path)

        job = ReportJob(
            id=row.report_id,
            conversation_id=row.conversation_id,
            title=row.title,
            intent=row.intent,
            skill=row.skill,
            html=html,
            status=row.status,
            error=row.error,
            created_at=row.created_at,
            finished_at=row.updated_at if row.status in ("ready", "failed") else "",
        )
        self._jobs[report_id] = job
        logger.info("report_loaded_from_db report_id=%s status=%s", report_id, row.status)
        return job

    async def alist_by_conversation(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = await self._db.list_reports_by_conversation(
            conversation_id,
            limit=limit,
        )
        return [
            {
                "report_id": r.report_id,
                "conversation_id": r.conversation_id,
                "title": r.title,
                "intent": r.intent,
                "skill": r.skill,
                "status": r.status,
                "html_size": r.html_size,
                "error": r.error,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rows
        ]

    # -- Persistence helpers (called by the generation pipeline) -------

    async def _persist_html(self, job: ReportJob) -> None:
        """Write job HTML to disk + update the DB row.

        Should be called when status transitions to ``ready`` or
        ``failed``. Skipped silently if HTML is empty (generation
        never reached the model response).
        """
        if job.status not in {"ready", "failed"}:
            return
        if not job.html and job.status != "failed":
            return

        html_file_name = f"{job.id}.html"
        html_path = self._reports_dir / html_file_name
        try:
            await asyncio.to_thread(self._write_html_sync, html_path, job.html)
            await self._db.update_report(
                job.id,
                status=job.status,
                html_file_path=html_file_name,
                html_size=len(job.html),
                error=job.error or None,
            )
            logger.info(
                "report_html_persisted report_id=%s status=%s size=%d",
                job.id,
                job.status,
                len(job.html),
            )
        except Exception:
            logger.exception("report_html_persist_failed report_id=%s", job.id)

    @staticmethod
    def _write_html_sync(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")


async def stream_report_artifact(
    *,
    job: ReportJob,
    request_id: str,
    store: "ReportJobStore | None" = None,
) -> AsyncIterator[dict[str, Any]]:
    _ensure_report_generation_task(job, request_id=request_id, store=store)
    event_index = 0

    while True:
        while event_index < len(job.events):
            yield _with_request_id(job.events[event_index], request_id)
            event_index += 1

        if job.status in {"ready", "failed"}:
            return
        if job.generation_task is not None and job.generation_task.done():
            return

        job.event_signal.clear()
        await job.event_signal.wait()


def _ensure_report_generation_task(
    job: ReportJob,
    *,
    request_id: str,
    store: "ReportJobStore | None" = None,
) -> None:
    if job.status in {"ready", "failed"}:
        return
    if job.generation_task is not None and not job.generation_task.done():
        return
    job.generation_task = asyncio.create_task(
        _collect_report_events(job=job, request_id=request_id, store=store)
    )


async def _collect_report_events(
    *,
    job: ReportJob,
    request_id: str,
    store: "ReportJobStore | None" = None,
) -> None:
    try:
        async for frame in _generate_report_artifact_frames(job=job, request_id=request_id):
            job.events.append(frame)
            job.event_signal.set()
        # Generation completed successfully — persist if store is available.
        if store is not None:
            await store._persist_html(job)
    except Exception as exc:
        logger.exception("report_background_generation_failed report_id=%s", job.id)
        job.status = "failed"
        job.error = f"HTML report generation failed: {exc}"
        job.finished_at = _utc_iso()
        job.events.append(_frame("artifact_error", job, request_id, {"message": job.error}))
        job.event_signal.set()
        if store is not None:
            await store._persist_html(job)
    finally:
        job.event_signal.set()


def _with_request_id(frame: dict[str, Any], request_id: str) -> dict[str, Any]:
    data = dict(frame.get("data") or {})
    data["request_id"] = request_id
    return {**frame, "data": data}


async def _generate_report_artifact_frames(
    *,
    job: ReportJob,
    request_id: str,
) -> AsyncIterator[dict[str, Any]]:
    if job.status == "ready":
        yield _frame("start", job, request_id, {"query": job.intent or job.title})
        yield _frame("step", job, request_id, {"turn": 1})
        yield _frame("artifact_start", job, request_id, {"type": "html", "title": job.title, "status": job.status})
        yield _frame("artifact_html_delta", job, request_id, {"delta": job.html})
        yield _frame("step_end", job, request_id, {"turn": 1, "has_tool_calls": False, "elapsed_ms": 0})
        yield _frame("artifact_ready", job, request_id, {"html_length": len(job.html)})
        yield _frame("artifact_export_ready", job, request_id, {"exports": job.exports or _export_urls(job.id)})
        return

    if job.status == "failed":
        yield _frame("start", job, request_id, {"query": job.intent or job.title})
        yield _frame("step", job, request_id, {"turn": 1})
        yield _frame("artifact_start", job, request_id, {"type": "html", "title": job.title, "status": job.status})
        yield _frame("artifact_error", job, request_id, {"message": job.error or "HTML report generation failed."})
        yield _frame("step_end", job, request_id, {"turn": 1, "has_tool_calls": False, "elapsed_ms": 0})
        return

    job.status = "running"
    job.error = ""
    job.html = ""
    job.exports = {}
    started_at = time.perf_counter()

    yield _frame("start", job, request_id, {"query": job.intent or job.title})
    yield _frame("step", job, request_id, {"turn": 1})
    yield _frame("artifact_start", job, request_id, {"type": "html", "title": job.title, "status": job.status})

    llm = create_llm_from_env(required=False)
    if llm is None:
        async for frame in _fail_report_generation(
            job=job,
            request_id=request_id,
            started_at=started_at,
            message="未配置大模型：请设置 LLM_API_KEY 与 LLM_MODEL 后再生成数据分析报告。",
        ):
            yield frame
        return

    messages = _build_agent_messages(job)

    try:
        streamed_chunks = 0
        raw_parts: list[str] = []
        # Models often start with prose like:
        #   "这是为您生成的报告：\n```html\n<!doctype html>..."
        # We buffer until we see <!doctype/<html, then emit only the
        # HTML body from that point on. Everything before is dropped so
        # the right-side preview never flashes the model's preamble.
        prelude_buffer = ""
        html_started = False
        async for chunk in llm.chat_stream(
            messages,
            temperature=0.15,
        ):
            if chunk.reasoning_content:
                yield _frame("thinking", job, request_id, {"delta": chunk.reasoning_content})
            if not chunk.content:
                continue
            raw_parts.append(chunk.content)
            streamed_chunks += 1

            if html_started:
                job.html += chunk.content
                yield _frame("artifact_html_delta", job, request_id, {"delta": chunk.content})
                continue

            prelude_buffer += chunk.content
            start_index = _find_html_start(prelude_buffer)
            if start_index is None:
                continue
            payload = prelude_buffer[start_index:]
            prelude_buffer = ""
            html_started = True
            job.html = payload
            yield _frame("artifact_html_delta", job, request_id, {"delta": payload})

        # Even if the stream finished without ever hitting <!doctype
        # (model gave only prose), surface whatever we have so the user
        # isn't left with an empty artifact.
        if not html_started and prelude_buffer:
            job.html = prelude_buffer

        final_html = extract_model_html("".join(raw_parts), title=job.title)
        if final_html != job.html:
            job.html = final_html
            yield _frame("artifact_html_delta", job, request_id, {"delta": final_html, "replace": True})
    except Exception as exc:
        logger.warning("report_html_generation_failed report_id=%s error=%s", job.id, exc)
        async for frame in _fail_report_generation(
            job=job,
            request_id=request_id,
            started_at=started_at,
            message=f"报告生成失败：{exc}",
        ):
            yield frame
        return

    job.status = "ready"
    job.finished_at = _utc_iso()
    job.exports = _export_urls(job.id)

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    yield _frame(
        "step_end",
        job,
        request_id,
        {"turn": 1, "has_tool_calls": False, "elapsed_ms": elapsed_ms},
    )
    yield _frame("artifact_ready", job, request_id, {"html_length": len(job.html)})
    yield _frame("artifact_export_ready", job, request_id, {"exports": job.exports})

    # Companion narration: short Chinese summary so the chat side isn't
    # silent after the artifact lands.
    yield _frame("step", job, request_id, {"turn": 2})
    try:
        async for frame in _stream_companion_summary(
            llm=llm,
            job=job,
            request_id=request_id,
        ):
            yield frame
    except Exception as exc:
        logger.warning("report_companion_summary_failed report_id=%s error=%s", job.id, exc)
    finally:
        close = getattr(llm, "close", None)
        if close is not None:
            with suppress(Exception):
                await close()

    yield _frame(
        "step_end",
        job,
        request_id,
        {
            "turn": 2,
            "has_tool_calls": False,
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000) - elapsed_ms,
        },
    )


async def _fail_report_generation(
    *,
    job: ReportJob,
    request_id: str,
    started_at: float,
    message: str,
) -> AsyncIterator[dict[str, Any]]:
    job.status = "failed"
    job.error = message
    job.finished_at = _utc_iso()
    yield _frame("artifact_error", job, request_id, {"message": message})
    yield _frame("text", job, request_id, {"delta": message + "\n"})
    yield _frame(
        "step_end",
        job,
        request_id,
        {
            "turn": 1,
            "has_tool_calls": False,
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            "error_type": "html_generation_failed",
        },
    )


async def _stream_companion_summary(
    *,
    llm: Any,
    job: ReportJob,
    request_id: str,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a short Chinese narration after the artifact is ready.

    The chat panel would otherwise be empty once HTML lands in the
    right-side artifact. This second LLM pass writes 200-400 zh-CN
    chars: what the report covers, 3-5 key findings, suggested next
    steps. It's intentionally short (max 800 tokens, temp 0.3) and
    plain text — no markdown, no emoji, no code fences.
    """
    file_context = _format_file_context_for_summary(job.file_briefs)
    system_prompt = (
        "你是一位资深数据分析师，刚刚为用户在右侧 artifact 区生成了一份 HTML 数据分析报告。"
        "现在请用中文写一段 200-400 字的口语化要点讲解，作为对话区的助手回复："
        "（1）一句话总结报告覆盖了什么；"
        "（2）列出 3-5 条关键发现，每条聚焦一个具体数字或趋势；"
        "（3）一句话提示用户接下来可以关注什么、或可以追问什么。"
        "禁止使用 markdown 语法（不写 #、*、-、```）、不要 emoji、不要照搬 HTML 内容。"
        "语气专业但不僵硬，像同事汇报。"
    )
    user_prompt = (
        f"用户诉求：{job.intent or job.title}\n"
        f"报告标题：{job.title}\n"
        f"\n以下是参考数据（不要照搬，只用来确认你提到的数字正确）：\n{file_context}"
    )
    summary_messages = [Message.system(system_prompt), Message.user(user_prompt)]

    async for chunk in llm.chat_stream(
        summary_messages,
        temperature=0.3,
        max_tokens=800,
    ):
        if chunk.content:
            yield _frame("text", job, request_id, {"delta": chunk.content})


def _format_file_context_for_summary(file_briefs: list[dict[str, Any]]) -> str:
    """Compact, single-line-per-file summary used by the companion narration.

    The narration step doesn't need the full prompt payload — just file
    names, kind, and a one-line gist so the model can pick concrete
    numbers from memory of the artifact it just generated.
    """
    lines: list[str] = []
    for index, brief in enumerate(file_briefs, start=1):
        sheets = brief.get("sheets") or []
        sheet_names = [str(s.get("name") or "") for s in sheets if isinstance(s, dict)]
        lines.append(
            f"- 文件 {index}: {brief.get('filename', '')} "
            f"(kind={brief.get('kind') or 'unknown'}, "
            f"sheets={sheet_names or '—'}, "
            f"pages={brief.get('page_count') or 0})"
        )
    return "\n".join(lines) if lines else "（无上传文件）"


def _find_html_start(buffer: str) -> int | None:
    """Locate the first byte of real HTML inside a possibly preamble-prefixed
    buffer.

    Models often emit a "这是为您生成的 HTML 报告…" preamble followed by
    a markdown fence ``\\u0060\\u0060\\u0060html`` and only then the actual
    ``<!doctype html>``. We want to drop everything before ``<!doctype``
    (case-insensitive) or, failing that, the first ``<html`` tag.
    Returns ``None`` if neither marker is present yet — caller should
    keep buffering.
    """
    lowered = buffer.lower()
    doctype_idx = lowered.find("<!doctype")
    html_idx = lowered.find("<html")
    candidates = [idx for idx in (doctype_idx, html_idx) if idx >= 0]
    if not candidates:
        return None
    return min(candidates)


def _build_agent_messages(job: ReportJob) -> list[Message]:
    system_prompt = _skill_prompt(job.skill)
    file_context = _format_file_context(job.file_briefs)
    resume_block = ""
    if job.resume_from_html.strip():
        # Include the prior (possibly partial) HTML so the model can
        # decide whether to continue from the breakpoint or rewrite
        # in place. The system prompt's "迭代与修改" section instructs
        # the model how to read this block.
        resume_block = (
            "\n# 上一版报告 HTML（可能未完成）\n"
            "下面是上一轮生成的报告内容。如果用户意图是「继续」「接着写」「补全」，"
            "请从此处的断点接着完成；如果是「修改」「调整」类意图，请基于此版本作差异化迭代，"
            "尽量保持设计风格与已有结构。\n"
            "```html\n"
            f"{job.resume_from_html}\n"
            "```\n"
        )
    user_prompt = (
        f"# 用户诉求\n{job.intent or job.title}\n\n"
        f"# 报告标题\n{job.title}\n\n"
        f"# 上传文件上下文\n{file_context or '本次未提供上传文件。若用户诉求依赖外部数据，请在报告中明确说明数据缺失，不要编造。'}\n"
        f"{resume_block}\n"
        "# 交付要求\n"
        "- 仅输出一个完整、独立的 HTML 文档：包含 `<!doctype html>`、`<html lang=\"zh-CN\">`、`<head>`（含 `<meta charset>`、`<title>`、内联 `<style>` 与 ECharts CDN `<script>`）和 `<body>`。\n"
        "- 必须按「内容骨架」产出 10 章，每章配齐图表（除封面外）与文字解读；任何章节都不可省略，缺数据时写「暂无相关数据」并说明缺什么。\n"
        "- 所有 ECharts 实例集中在文档末尾一个 `<script>` 块中初始化（`DOMContentLoaded` 之后），变量命名 `chart_1, chart_2, ...` 与对应容器 id 一致。\n"
        "- 严格遵循系统提示中的「设计系统」「图表实现规范」「数字规范」「禁止事项」。\n"
        "- 所有数字必须来自上方「上传文件上下文」；引用时在图表的 `数据来源` 注脚或正文括号中注明（文件名/sheet/行号）。\n"
        "- 不要使用 markdown 代码围栏（```），不要在 HTML 之外输出任何解释、思考或前后缀。"
    )
    return [Message.system(system_prompt), Message.user(user_prompt)]


_DESIGN_SYSTEM = """## 设计系统（严格遵守）
- 字体栈：`-apple-system, "PingFang SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif`
- 主色：`#0F4C81`；辅色：`#E8F0FB`；文本：`#1A2332`；弱化文本：`#6B7785`；分隔线：`#E5EAF2`
- 上升：`#1E8E5A`，下降：`#C0392B`，警示：`#B8860B`
- 间距体系：`8 / 16 / 24 / 40 px` 倍数；卡片 padding `24px`；章节间距 `40px`
- 字号：H1 `28px`/H2 `22px`/H3 `18px`/正文 `14px`/注释 `12px`；行高 `1.65`
- 卡片：背景 `#FFF`、圆角 `8px`、阴影 `0 1px 3px rgba(0,0,0,.06)`、边框 `1px solid #E5EAF2`
- 表格：表头底色 `#0F4C81` 白字、斑马纹 `#F8FAFD`、单元格 padding `8px 12px`、数字右对齐
- KPI 卡片网格：3-4 列；每张含标签 + 数值 + 同比/环比 delta + 上下箭头
- 图表配色（按顺序）：`#0F4C81, #3B82C7, #7FB3E0, #B7D5EE, #DFE9F4, #6B7785`
- 章节标题：H2 左侧 4px 主色色块前缀，顶部 `40px` 间距
- 页面容器：`<main class="page">` 宽度 `min(100%, 920px)`、白底、上下内边距 `48px 56px`、外层背景 `#EEF2F7`"""


_CONTENT_SKELETON = """## 内容骨架（10 章，按顺序产出，缺数据则该章节写「暂无相关数据」+ 说明哪份数据缺失）

每章必须满足：**至少 1 张 ECharts 图表（除非显式说明用 KPI 卡片）+ 章节后段含解读文字（字数下限见各章）+ 数据来源明确到 文件名/sheet/行号**。

1. **封面区**：报告标题（H1）、副标题（数据期间或公司+场景）、生成时间、一句话核心结论。无图表。
2. **执行摘要**：5-8 张 KPI 卡片（金额/同比/环比/趋势箭头）+ 200-300 字综合结论。覆盖：营收/净利润/毛利率/经营现金流/资产负债率/ROE 等。
3. **经营业绩分析**：双轴折线柱状图（本期 vs 上期柱+同比%折线）+ 400-600 字。覆盖营收、净利、毛利、各项费用同比对比。
4. **成本与费用结构**：堆叠柱图（营业成本 / 销售 / 管理 / 研发 / 财务费用）+ 费用率横向条形 + 300-500 字。指出费用率变化、成本压力来源。
5. **资产结构分析**：饼图（流动 vs 非流动占比）+ 横向条形（前 10 大资产项）+ 300-500 字。指出资产配置变化、结构性变动。
6. **负债与权益结构**：堆叠柱图（流动负债 / 非流动负债 / 所有者权益）+ 流动比率/速动比率/资产负债率三仪表盘 + 300-500 字偿债能力评价。
7. **现金流量质量**：瀑布图（经营 / 投资 / 筹资 / 净增加额）+ 经营现金流/净利润比折线 + 400-600 字。判断利润含金量。
8. **重要变动项 Top 10**：横向条形（按变动率绝对值排序，前 10 项）+ 配套数据表 + 300-500 字归因分析（业务/会计/一次性事件）。
9. **财务比率仪表盘**：6-8 个 ECharts 仪表图（gauge）分四组：盈利能力（毛利率/净利率/ROE）/ 营运能力（应收周转/存货周转）/ 偿债能力（流动比/资产负债率）/ 成长能力（营收增速/利润增速）+ 200-300 字总评。
10. **风险提示与数据附录**：风险要点 5-8 条（每条具体到指标变化幅度）+ 数据附录（引用文件名、sheet 名、关键字段、数据期间、单位换算说明）+ 报告生成时间与免责声明。"""


_ECHARTS_GUIDELINES = """## 图表实现规范（ECharts 5.x，CDN 加载）

### 引入方式（必须放在 <head>）
```html
<script src=\"https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js\"></script>
```

### 每张图的标准容器
```html
<div class=\"chart-card\">
  <div class=\"chart-title\">图 N. xxx</div>
  <div id=\"chart_N\" style=\"width:100%;height:360px;\"></div>
  <div class=\"chart-source\">数据来源：文件名 / sheet / 行号</div>
</div>
```
图表初始化必须在 `DOMContentLoaded` 后，多图统一在一个 `<script>` 块内串行 init。

### 配色（必须用，不要自创）
```js
const COLOR_PALETTE = ['#0F4C81','#3B82C7','#7FB3E0','#B7D5EE','#DFE9F4','#6B7785'];
const COLOR_UP = '#1E8E5A';   // 同比上升
const COLOR_DOWN = '#C0392B'; // 同比下降
```

### 各图表类型 ECharts 配置要点

- **双轴对比图**（业绩同比）：`xAxis` 分类轴，`yAxis` 双轴（左金额、右百分比），`series` 两个柱状（本期/上期）+ 一个折线（同比%），折线放右轴。
- **堆叠柱图**（费用结构）：`series` 多个 `type:'bar'` 设置相同 `stack:'total'`，按 COLOR_PALETTE 顺序配色。
- **横向条形图**（Top 10）：`xAxis` 数值，`yAxis` 分类（`inverse:true`），`series.type:'bar'`，正值用主色、负值用 COLOR_DOWN。
- **瀑布图**（现金流）：用透明 placeholder series + 真实 series 叠加。正值 COLOR_UP，负值 COLOR_DOWN，合计 COLOR_PALETTE[0]。可参考 ECharts 官方 waterfall demo。
- **饼图**（资产结构）：`series.type:'pie'`，`radius:['40%','70%']` 做成环形；中心 `graphic.text` 显示合计金额。
- **仪表盘**（财务比率）：`series.type:'gauge'`，`min/max` 按指标合理范围（如资产负债率 0-100%、ROE -20%-40%），指针 + 当前值文本。
- **雷达图**（多维评分）：`radar.indicator` 列出维度，`series.type:'radar'` 双数据集对比本期 vs 上期。

### 通用规则
- 所有金额自动按量级转换：>1 亿用「亿元」、>1 万用「万元」、否则用「元」。在 `yAxis.axisLabel.formatter` / `tooltip.formatter` 中实现。
- 每图必有 `title.text`、`tooltip.trigger:'axis' 或 'item'`、`legend`（多 series 时）、`grid` 留 12% 边距防溢出。
- 禁止动画干扰打印：`animation: false`。
- 容器固定高度 320-400px，宽度 100%。
"""


_NUMBER_RULES = """## 数字规范
- 金额：根据量级选择「元 / 万元 / 亿元」，千分位、保留 2 位小数（如 `¥1,234.56 万`）
- 百分比：1 位小数（如 `12.3%`）
- 同比 / 环比：用 `↑` 绿色 / `↓` 红色 箭头标记方向；零变化用 `→` 灰色
- 日期：`YYYY-MM-DD` 或 `YYYY 年 MM 月`，全报告统一
- 缺失值：写「—」，不要写 `null` / `NaN` / `0`"""


_CHART_RULES = """## 图表选型规则
- 数据点 >12 个用折线、≤6 个用柱状/饼图、占比对比用堆叠柱图、多分类排名用横向条形、构成变化用瀑布、单值评估用仪表盘、多维对比用雷达
- 每张图必含：标题（图 N. xxx）、坐标轴标签、tooltip、数据来源注释（小字 `#6B7785`）
- 配色严格使用「设计系统」与「图表实现规范」中给出的 COLOR_PALETTE，按序使用，禁止自创颜色
- 同比/环比上升用 `COLOR_UP (#1E8E5A)`，下降用 `COLOR_DOWN (#C0392B)`"""


_PROHIBITIONS = """## 禁止事项
- 编造数字、虚构数据来源（任何数字必须能在「上传文件上下文」中找到）
- emoji、外链字体（Google Fonts 等）、外部 CSS、远程 `<img src>`
- markdown 代码围栏包裹 HTML
- 在 HTML 文档外输出任何说明、思考或前后缀
- 使用霓虹色、渐变彩虹、装饰性 emoji 图标
- 加载 ECharts 之外的任意第三方 script（CDN 仅限 jsdelivr / cdnjs / unpkg / fastly.jsdelivr 上的 echarts.min.js）"""


def _skill_prompt(skill: str) -> str:
    if skill == "data_analysis":
        role = (
            "# 角色\n"
            "你是一位资深财务与数据分析报告设计师，长期服务于企业 CFO、投资人、运营负责人。"
            "你的输出会被直接用于内部汇报、董事会材料或对外披露，质量标准对标德勤、麦肯锡级别的咨询交付物。"
            "默认使用简洁、克制的中文商业写作风格，避免口语化与营销腔。\n"
        )
    else:
        role = (
            "# 角色\n"
            "你是一位资深 HTML 报告设计师，擅长把用户的诉求与上传数据转化为可直接打印或汇报的精致单页文档。"
            "默认使用中文，保持克制、专业的版面风格。\n"
        )

    workflow = (
        "## 迭代与修改\n"
        "如果用户诉求中出现「修改」「调整」「增加」「去掉」「换成」「再做一版」「改一下」等迭代信号，"
        "且对话中已存在上一版报告（或本次 prompt 末尾附带了上一版 HTML），请：\n"
        "- 沿用上一版的整体设计 token（字体、配色、间距、章节标题样式）；\n"
        "- 仅针对用户提到的部分作差异化处理，其它章节保持结构和措辞不变；\n"
        "- 不要重新生成完全不同风格的报告。\n"
        "如果用户诉求是「继续」「接着写」等补全信号，且 prompt 末尾附带了上一版未完成的 HTML，"
        "请直接从该 HTML 的断点处接着补全到 10 章完整结构，并保持已有内容连贯。\n\n"
        "## 工作流程\n"
        "1. 通读「上传文件上下文」中所有文件的完整内容（CSV 全部数据行、PDF/DOC/PPT 全文）——这是事实唯一来源。\n"
        "2. 如果某文件头部出现「⚠️ 已截断」提示，意味着原文超出上下文上限、仅前 N 字符可见；在「数据附录」中明确告知用户哪个文件不完整。\n"
        "3. 按「内容骨架」7 章规划：每个章节标注数据来源（文件名/sheet/行号）；缺数据的章节写「暂无相关数据」，不要编造。\n"
        "4. 撰写 HTML 时严格套用「设计系统」token，不要发明新颜色/字号。\n"
        "5. 完稿前自检：数字是否标注来源、章节是否齐全、配色与字号是否一致、是否混入 markdown 围栏或解释性文字。\n"
    )

    return "\n\n".join(
        [
            role,
            _DESIGN_SYSTEM,
            _CONTENT_SKELETON,
            _NUMBER_RULES,
            _CHART_RULES,
            _ECHARTS_GUIDELINES,
            _PROHIBITIONS,
            workflow,
        ]
    )


def extract_model_html(content: str, *, title: str = "Report") -> str:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:html)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    doctype_idx = text.lower().find("<!doctype")
    html_idx = text.lower().find("<html")
    start_candidates = [idx for idx in (doctype_idx, html_idx) if idx >= 0]
    if start_candidates:
        start = min(start_candidates)
        end = text.lower().rfind("</html>")
        text = text[start : end + len("</html>")] if end >= start else text[start:]
    elif "<" in text and ">" in text:
        text = _html_document(title, text)
    else:
        raise ValueError("model response did not contain HTML")
    return _sanitize_html(text)


def _html_document(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, "Microsoft YaHei", "PingFang SC", Arial, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #eef2f7; color: #172033; line-height: 1.65; }}
    .page {{ width: min(100%, 920px); margin: 0 auto; background: #fff; min-height: 100vh; padding: 48px 56px; }}
    @media print {{ body {{ background: #fff; }} .page {{ width: auto; padding: 0; }} }}
    @media (max-width: 760px) {{ .page {{ padding: 30px 22px; }} }}
  </style>
</head>
<body>
  <main class="page">
    {body}
  </main>
</body>
</html>"""


# Token budget for the full uploaded-file payload. The target model is
# Gemini 2.0-class with a ~1M token window; we reserve 5% for the
# system prompt + user intent + delivery instructions + generated HTML
# headroom, leaving ~950k tokens for the file context itself.
TOTAL_CONTEXT_TOKEN_BUDGET = 1_000_000
RESERVED_TOKEN_FRACTION = 0.05
FILE_CONTEXT_TOKEN_BUDGET = int(
    TOTAL_CONTEXT_TOKEN_BUDGET * (1 - RESERVED_TOKEN_FRACTION)
)
# Char→token estimate. CJK leans 1 char ≈ 1 token; English/CSV leans
# 1 char ≈ 0.3 token. Mixed content averages ~0.5, which is what we use
# for budgeting. Slight over-estimation is intentional.
CHARS_PER_TOKEN = 2  # so char_budget = tokens * 2
FILE_CONTEXT_CHAR_BUDGET = FILE_CONTEXT_TOKEN_BUDGET * CHARS_PER_TOKEN


def _format_file_context(file_briefs: list[dict[str, Any]]) -> str:
    """Dump every uploaded file in full into the prompt.

    Strategy:
    1. Load each file's full payload (sheets as CSV, text-based files as
       extracted text, images as a one-line metadata stub).
    2. Sum the total character count. If under budget, ship as-is.
    3. If over budget, allocate to each file proportionally to its
       original size and truncate the tail with a clear marker so the
       model knows the content is incomplete.
    """
    if not file_briefs:
        return ""

    project_root = _project_root()
    payloads = [_load_full_payload(brief, project_root) for brief in file_briefs]
    total = sum(len(p.body) for p in payloads)

    if total > FILE_CONTEXT_CHAR_BUDGET:
        _truncate_payloads_in_place(payloads, FILE_CONTEXT_CHAR_BUDGET)

    chunks: list[str] = []
    for index, payload in enumerate(payloads, start=1):
        header_lines = [
            f"## 文件 {index}: {payload.filename}",
            f"- file_id: `{payload.file_id}`",
            f"- 类型: {payload.kind}",
            f"- 大小: {payload.size_bytes} bytes",
        ]
        if payload.page_count:
            header_lines.append(f"- 页数/页签: {payload.page_count}")
        if payload.warnings:
            header_lines.append(f"- warnings: {json.dumps(payload.warnings, ensure_ascii=False)}")
        if payload.truncated:
            header_lines.append(
                f"- ⚠️ 已截断：原文 {payload.original_chars} 字符，prompt 中仅保留前 {len(payload.body)} 字符。"
                f"超出部分对本次报告不可见，请在「数据附录」明确告知用户。"
            )
        chunks.append("\n".join(header_lines) + "\n" + payload.body)

    return "\n\n".join(chunks)


@dataclass(slots=True)
class _FilePayload:
    file_id: str
    filename: str
    kind: str
    size_bytes: int
    page_count: int
    warnings: list[Any]
    body: str
    original_chars: int
    truncated: bool = False


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _load_full_payload(brief: dict[str, Any], project_root: Path) -> _FilePayload:
    """Build the in-prompt body for one uploaded file."""
    kind = str(brief.get("kind") or "unknown")
    filename = str(brief.get("filename") or "")
    file_id = str(brief.get("file_id") or "")
    sections: list[str] = []

    if kind == "table":
        sheets = brief.get("sheets", []) or []
        for sheet in sheets:
            if not isinstance(sheet, dict):
                continue
            csv_path = str(sheet.get("full_csv_path") or "").strip()
            sheet_name = str(sheet.get("name") or "")
            row_count = int(sheet.get("row_count") or 0)
            col_count = int(sheet.get("column_count") or 0)
            sections.append(f"### sheet `{sheet_name}` (行数={row_count}, 列数={col_count})")
            csv_text = _read_artifact(project_root, csv_path)
            if csv_text:
                sections.append("```csv\n" + csv_text.rstrip() + "\n```")
            else:
                # Fallback: preview rows already in the brief.
                headers = sheet.get("headers", []) or []
                rows = sheet.get("rows", []) or []
                if headers:
                    sections.append(",".join(str(h) for h in headers))
                for row in rows:
                    sections.append(",".join(str(cell) for cell in row))
    elif kind in {"pdf", "document", "slides", "text"}:
        full_path = str(brief.get("text_full_path") or "").strip()
        full_text = _read_artifact(project_root, full_path)
        if not full_text:
            full_text = str(brief.get("text_preview") or "")
        if full_text:
            sections.append("```text\n" + full_text + "\n```")
    elif kind == "image":
        sections.append(str(brief.get("text_preview") or "image (no metadata)"))
    else:
        # Unknown kind: dump whatever preview we have.
        preview = str(brief.get("text_preview") or "")
        if preview:
            sections.append("```text\n" + preview + "\n```")

    body = "\n".join(sections).strip()
    return _FilePayload(
        file_id=file_id,
        filename=filename,
        kind=kind,
        size_bytes=int(brief.get("size_bytes") or 0),
        page_count=int(brief.get("page_count") or 0),
        warnings=list(brief.get("warnings") or []),
        body=body,
        original_chars=len(body),
    )


def _read_artifact(project_root: Path, relative_path: str) -> str:
    if not relative_path:
        return ""
    candidate = project_root / relative_path
    if not candidate.exists():
        # Fall back to interpreting the path as absolute (older parser
        # versions returned absolute paths on Windows when the file was
        # outside the project root).
        candidate = Path(relative_path)
        if not candidate.exists():
            return ""
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError:
        return ""


def _truncate_payloads_in_place(payloads: list[_FilePayload], budget: int) -> None:
    """Cut each payload proportionally to its original size to fit ``budget``.

    A file that was 100KB of an 800KB total gets 1/8 of the budget. Each
    file is guaranteed at least a small floor (5k chars) so even the
    smallest file still contributes something.
    """
    total = sum(p.original_chars for p in payloads)
    if total <= 0:
        return
    floor = min(5_000, budget // max(1, len(payloads)))
    # First pass: assign each payload a share, clamped to its actual size.
    shares: list[int] = []
    for payload in payloads:
        proportional = int(budget * payload.original_chars / total)
        shares.append(max(floor, min(payload.original_chars, proportional)))
    # If the floor pushed us over budget, scale everything down evenly.
    granted = sum(shares)
    if granted > budget:
        factor = budget / granted
        shares = [int(share * factor) for share in shares]

    for payload, share in zip(payloads, shares, strict=True):
        if share >= payload.original_chars:
            continue
        payload.body = payload.body[:share] + "\n\n…[内容因上下文上限被截断]"
        payload.truncated = True


# Trusted CDN origins for external scripts (ECharts and friends).
# Any <script src="..."> outside this allow-list is stripped during
# sanitisation. Inline <script>...</script> blocks are allowed because
# we need the model to drive ECharts via JS; those scripts run in the
# user's own browser against trusted content the user generated.
_TRUSTED_SCRIPT_HOSTS = (
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
    "unpkg.com",
    "fastly.jsdelivr.net",
)


def _sanitize_html(value: str) -> str:
    # 1. Strip inline event handlers (onclick=, onerror=, ...).
    value = re.sub(r"\son[a-z]+\s*=\s*(['\"]).*?\1", "", value, flags=re.DOTALL | re.IGNORECASE)
    # 2. Strip javascript: URLs in href/src.
    value = re.sub(
        r"\s(?:href|src)\s*=\s*(['\"])\s*javascript:.*?\1",
        "",
        value,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # 3. Remove <script src="..."> tags whose host is not in the allow-list.
    #    Inline <script> (no src attribute) is kept.
    value = re.sub(
        r"<script\b([^>]*?)\bsrc\s*=\s*(['\"])(.*?)\2([^>]*)>\s*</script>",
        lambda m: m.group(0) if _is_trusted_script_src(m.group(3)) else "",
        value,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return value


def _is_trusted_script_src(src: str) -> bool:
    src_lower = src.strip().lower()
    if not src_lower.startswith(("https://", "http://", "//")):
        return False
    # Strip scheme for host comparison.
    bare = src_lower.split("://", 1)[-1]
    host = bare.split("/", 1)[0]
    return any(host == h or host.endswith("." + h) for h in _TRUSTED_SCRIPT_HOSTS)


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _frame(
    event: str,
    job: ReportJob,
    request_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "artifact_id": job.id,
        "report_id": job.id,
        "conversation_id": job.public_conversation_id or job.conversation_id,
        "request_id": request_id,
        **data,
    }
    return {"event": event, "data": payload}


def _export_urls(report_id: str) -> dict[str, str]:
    return {
        "html": f"/api/reports/{report_id}/exports/html",
        "pdf": f"/api/reports/{report_id}/exports/pdf",
        "word": f"/api/reports/{report_id}/exports/word",
        "md": f"/api/reports/{report_id}/exports/md",
    }


def _file_brief(record: ReportFileRecord) -> dict[str, Any]:
    parsed = record.parsed.model_dump(mode="json")
    return {
        "file_id": record.id,
        "filename": record.filename,
        "size_bytes": record.size_bytes,
        "kind": parsed.get("kind", "unknown"),
        "sheets": parsed.get("sheets", []),
        "text_preview": parsed.get("text_preview", ""),
        "text_full_path": parsed.get("text_full_path", ""),
        "text_total_chars": parsed.get("text_total_chars", 0),
        "page_count": parsed.get("page_count", 0),
        "warnings": parsed.get("warnings", []),
    }
