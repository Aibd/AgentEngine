# 数据分析报告功能 — 前后端 API 契约

> 版本: v1.0 | 更新日期: 2026-05-26
> 后端: Python FastAPI (AgentEngine)
> 前端: Vue 3 + TypeScript (gjsk-aiagentplatform)

---

## 1. 功能概述

用户上传财务文件（CSV/XLSX/PDF），后端解析数据、计算 KPI、调用 LLM 流式生成 HTML 报告，前端实时展示并支持导出。

**调用流程：**

```
上传文件 ──→ 创建报告任务 ──→ SSE 流式接收 ──→ 渲染报告 ──→ 导出下载
  (1)          (2)             (3)           (4)         (5)
```

---

## 2. 接口总览

| # | 方法 | 路径 | 说明 |
|---|------|------|------|
| 1 | POST | `/api/report-files` | 上传文件并解析 |
| 2 | GET | `/api/report-files/{file_id}` | 获取文件解析结果 |
| 3 | POST | `/api/reports` | 创建报告任务 |
| 4 | GET | `/api/reports/{report_id}` | 获取报告快照/状态 |
| 5 | GET | `/api/reports/{report_id}/stream` | SSE 流式生成报告 |
| 6 | GET | `/api/reports/{report_id}/charts/{chart_id}.svg` | 获取图表 SVG |
| 7 | GET | `/api/reports/{report_id}/exports/{format}` | 导出报告文件 |

---

## 3. 接口详情

### 3.1 上传文件

```
POST /api/report-files?filename={filename}&conversation_id={cid}
Content-Type: application/octet-stream
Body: <文件二进制内容>
```

**Query 参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| filename | string | 是 | 原始文件名，含扩展名 |
| conversation_id | string | 否 | 默认 `"web-conversation"` |
| tenant_id | string | 否 | 默认 `"default"` |

**成功响应 `200`：**

```json
{
  "id": "file_abc123",
  "filename": "revenue_2025.xlsx",
  "size_bytes": 102400,
  "parsed": {
    "file_id": "file_abc123",
    "filename": "revenue_2025.xlsx",
    "extension": ".xlsx",
    "size_bytes": 102400,
    "sheets": [
      {
        "name": "Sheet1",
        "headers": ["月份", "营收", "净利润", "同比增长"],
        "rows": [
          ["2025-01", "1200000", "350000", "0.15"],
          ["2025-02", "1350000", "400000", "0.22"]
        ],
        "row_count": 12,
        "column_count": 4
      }
    ],
    "text_preview": "",
    "page_count": 0,
    "warnings": []
  }
}
```

**错误响应：**

| 状态码 | 说明 |
|--------|------|
| 415 | 不支持的文件扩展名（仅支持 .csv, .xlsx, .xls, .pdf） |
| 413 | 文件过大（上限 50MB） |
| 400 | 其他解析错误 |

---

### 3.2 获取文件解析结果

```
GET /api/report-files/{file_id}
```

**响应：** 同 3.1 的 `parsed` 结构。

---

### 3.3 创建报告任务

```
POST /api/reports
Content-Type: application/json

{
  "title": "2025年Q1财务分析报告",
  "intent": "分析营收趋势和利润变化",
  "skill": "data_analysis",
  "file_ids": ["file_abc123", "file_def456"],
  "conversation_id": "web-conversation",
  "tenant_id": "default"
}
```

**请求体字段：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| title | string | 是 | 报告标题 |
| intent | string | 否 | 用户意图描述，指导 LLM 分析方向 |
| skill | string | 否 | 技能名，默认 `"data_analysis"` |
| file_ids | string[] | 否 | 文件 ID 列表；为空则使用 conversation 下所有文件 |
| conversation_id | string | 否 | 默认 `"web-conversation"` |
| tenant_id | string | 否 | 默认 `"default"` |

**成功响应 `200`：**

```json
{
  "id": "report_xyz789",
  "conversation_id": "web-conversation",
  "title": "2025年Q1财务分析报告",
  "intent": "分析营收趋势和利润变化",
  "skill": "data_analysis",
  "status": "pending",
  "html": "",
  "error": null,
  "chart_assets": {},
  "exports": {},
  "file_ids": ["file_abc123", "file_def456"],
  "created_at": "2026-05-26T10:30:00Z"
}
```

**错误响应：**

| 状态码 | 说明 |
|--------|------|
| 404 | `detail.missing_file_ids` — 指定的文件 ID 不存在 |
| 400 | 缺少必填字段 |

---

### 3.4 获取报告快照

```
GET /api/reports/{report_id}
```

**响应：** 同 3.3 的响应结构。`status` 字段取值：

| 值 | 说明 |
|----|------|
| `pending` | 等待生成 |
| `running` | 正在生成（可通过 stream 监听） |
| `completed` | 生成完成，`html` 字段有内容 |
| `failed` | 生成失败，`error` 字段有错误信息 |

---

### 3.5 SSE 流式生成（核心接口）

```
GET /api/reports/{report_id}/stream
Accept: text/event-stream
```

**SSE 协议格式：**

```
event: {事件类型}
data: {JSON 数据}

```

每个事件以 `event:` 开头接事件类型，`data:` 开头接 JSON 字符串，事件之间以空行 `\n\n` 分隔。

**事件类型一览：**

| 事件 | 何时触发 | data 关键字段 |
|------|---------|--------------|
| `artifact_start` | 报告生成开始 | `artifact_id`, `report_id`, `title`, `type` |
| `artifact_section_started` | 新章节开始 | `artifact_id`, `name` |
| `artifact_html_delta` | HTML 增量片段 | `artifact_id`, `delta`, `replace?` |
| `artifact_chart_ready` | 图表生成完成 | `artifact_id`, `chart_id`, `preview_url`, `title` |
| `artifact_ready` | 报告生成完成 | `artifact_id`, `report_id` |
| `artifact_export_ready` | 导出文件就绪 | `artifact_id`, `exports` |
| `artifact_error` | 生成出错 | `artifact_id`, `message` |

**终止事件（收到后关闭连接）：** `artifact_ready`、`artifact_error`

---

#### 3.5.1 事件 data 详细结构

**artifact_start：**
```json
{
  "artifact_id": "artifact_001",
  "report_id": "report_xyz789",
  "title": "2025年Q1财务分析报告",
  "type": "financial_report",
  "html": ""
}
```

`type` 取值：`"financial_report"` | `"html"` | `"dashboard"`

**artifact_section_started：**
```json
{
  "artifact_id": "artifact_001",
  "name": "营收趋势分析"
}
```

**artifact_html_delta：**
```json
{
  "artifact_id": "artifact_001",
  "delta": "<section><h2>营收趋势</h2><p>本月营收同比增长15%...</p></section>",
  "replace": false
}
```

- `replace: false`（默认）— 追加到已有 HTML
- `replace: true` — 替换整个 HTML

**artifact_chart_ready：**
```json
{
  "artifact_id": "artifact_001",
  "chart_id": "chart_revenue_trend",
  "preview_url": "/api/reports/report_xyz789/charts/chart_revenue_trend.svg",
  "title": "营收趋势图"
}
```

**artifact_export_ready：**
```json
{
  "artifact_id": "artifact_001",
  "exports": {
    "html": "/api/reports/report_xyz789/exports/html",
    "pdf": "/api/reports/report_xyz789/exports/pdf",
    "docx": "/api/reports/report_xyz789/exports/docx",
    "md": "/api/reports/report_xyz789/exports/md"
  }
}
```

**artifact_error：**
```json
{
  "artifact_id": "artifact_001",
  "message": "LLM 生成超时"
}
```

---

### 3.6 获取图表 SVG

```
GET /api/reports/{report_id}/charts/{chart_id}.svg
Accept: image/svg+xml
```

**响应：** `200` — SVG XML 内容（`Content-Type: image/svg+xml; charset=utf-8`）

可直接用于 `<img src="...">` 或 `<object>` 标签。

---

### 3.7 导出报告

```
GET /api/reports/{report_id}/exports/{format}
```

**format 取值：**

| 值 | Content-Type | 说明 |
|----|-------------|------|
| `html` | text/html | HTML 文件下载 |
| `pdf` | application/pdf | PDF 文件下载 |
| `word` / `doc` / `docx` | application/vnd.openxmlformats-officedocument.wordprocessingml.document | Word 文件下载 |
| `md` | text/markdown | Markdown 文本下载 |

**响应头包含** `Content-Disposition: attachment; filename="{report_id}.{ext}"`，浏览器会触发文件下载。

**错误响应：**

| 状态码 | 说明 |
|--------|------|
| 409 | 报告 HTML 尚未生成完成 |
| 501 | 该导出格式暂不支持（如 PDF 依赖未安装） |

---

## 4. TypeScript 类型定义

前端可直接使用以下类型：

```typescript
// ── 文件上传 ──

interface ParsedSheetPreview {
  name: string;
  headers: string[];
  rows: string[][];
  row_count: number;
  column_count: number;
}

interface ParsedReportFile {
  file_id: string;
  filename: string;
  extension: string;
  size_bytes: number;
  sheets: ParsedSheetPreview[];
  text_preview: string;
  page_count: number;
  warnings: string[];
}

interface ReportFileSummary {
  id: string;
  filename: string;
  size_bytes: number;
  parsed: ParsedReportFile;
}

// ── 报告任务 ──

interface ReportJobSnapshot {
  id: string;
  conversation_id: string;
  title: string;
  intent: string;
  skill: string;
  status: "pending" | "running" | "completed" | "failed";
  html: string;
  error: string | null;
  chart_assets: Record<string, string>;  // chart_id → SVG content
  exports: Record<string, string>;       // format → URL
  file_ids: string[];
  created_at: string;
}

// ── SSE 事件 ──

type ReportSseEventType =
  | "artifact_start"
  | "artifact_section_started"
  | "artifact_html_delta"
  | "artifact_chart_ready"
  | "artifact_ready"
  | "artifact_export_ready"
  | "artifact_error";

interface ReportSseEvent {
  event: ReportSseEventType;
  data: Record<string, unknown>;
}

// ── Artifact 状态（前端维护） ──

interface ArtifactTrace {
  id: string;
  reportId: string;
  type: "financial_report" | "html" | "dashboard";
  title: string;
  status: "streaming" | "ready" | "failed";
  currentSection?: string;
  html: string;
  charts: Record<string, { url?: string; title?: string }>;
  exports?: Record<string, string>;
  error?: string;
}
```

---

## 5. 前端接入指南

### 5.1 新增 axios 实例（或 fetch 封装）

推荐在 `src/utils/api/instances/` 新增报告服务实例，或复用已有的 `pythonApi` 实例。API 基础路径与现有 AI 服务一致。

### 5.2 新增 API 模块 `src/utils/api/modules/report.ts`

```typescript
import { apiClient } from "../instances"; // 根据实际项目调整

// 上传文件
export async function uploadReportFile(
  file: File,
  conversationId = "web-conversation",
): Promise<ReportFileSummary> {
  const params = new URLSearchParams({
    filename: file.name,
    conversation_id: conversationId,
  });
  const response = await fetch(`/api/report-files?${params}`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  });
  if (!response.ok) throw new Error(`upload failed: ${response.status}`);
  return response.json();
}

// 创建报告
export async function createReport(params: {
  title: string;
  intent?: string;
  skill?: string;
  fileIds: string[];
  conversationId?: string;
}): Promise<ReportJobSnapshot> {
  const res = await fetch("/api/reports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: params.title,
      intent: params.intent ?? "",
      skill: params.skill ?? "data_analysis",
      file_ids: params.fileIds,
      conversation_id: params.conversationId ?? "web-conversation",
    }),
  });
  if (!res.ok) throw new Error(`create report failed: ${res.status}`);
  return res.json();
}

// SSE 流式消费（推荐复用项目已有的 SSE 解析逻辑）
export function streamReport(
  reportId: string,
  onEvent: (event: ReportSseEvent) => void,
  onDone: () => void,
): () => void {
  const controller = new AbortController();

  (async () => {
    const response = await fetch(`/api/reports/${reportId}/stream`, {
      headers: { Accept: "text/event-stream" },
      signal: controller.signal,
    });
    if (!response.ok || !response.body) throw new Error("stream failed");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        const event = parseSsePart(part);
        if (event) onEvent(event);
      }
    }
    onDone();
  })().catch(() => onDone());

  return () => controller.abort();
}

function parseSsePart(part: string): ReportSseEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of part.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  return {
    event: event as ReportSseEventType,
    data: JSON.parse(dataLines.join("\n")),
  };
}

// 导出下载（直接跳转浏览器下载）
export function downloadReport(reportId: string, format: string): void {
  window.open(`/api/reports/${reportId}/exports/${format}`, "_blank");
}

// 获取图表 SVG URL
export function chartUrl(reportId: string, chartId: string): string {
  return `/api/reports/${reportId}/charts/${chartId}.svg`;
}
```

### 5.3 SSE 事件状态管理（参考实现）

建议维护一个 `ArtifactTrace` 状态对象，根据 SSE 事件更新：

```
artifact_start           → 创建 ArtifactTrace，status = "streaming"
artifact_section_started → 更新 currentSection
artifact_html_delta      → 追加/替换 html 字段
artifact_chart_ready     → 记录 chart URL
artifact_ready           → status = "ready"
artifact_export_ready    → 记录导出 URL
artifact_error           → status = "failed"，记录 error
```

### 5.4 报告渲染

报告 HTML 是自包含的完整页面，推荐用 **sandboxed iframe** 渲染：

```vue
<template>
  <iframe
    :srcdoc="artifact.html"
    sandbox="allow-same-origin"
    class="report-frame"
  />
</template>
```

---

## 6. 完整调用时序

```
前端                                    后端
 │                                       │
 │  1. POST /api/report-files            │
 │  (上传文件，可多次)                    │
 │  ─────────────────────────────────→   │
 │  ← 200 { id, parsed }                 │
 │                                       │
 │  2. POST /api/reports                 │
 │  { title, intent, file_ids }          │
 │  ─────────────────────────────────→   │
 │  ← 200 { id: "report_xyz", status }  │
 │                                       │
 │  3. GET /api/reports/report_xyz/stream│
 │  ─────────────────────────────────→   │
 │                                       │
 │  ← event: artifact_start              │
 │  ← event: artifact_section_started    │
 │  ← event: artifact_html_delta  (多次) │
 │  ← event: artifact_chart_ready        │
 │  ← event: artifact_html_delta  (多次) │
 │  ← event: artifact_ready              │  ← 终止事件，关闭连接
 │  ← event: artifact_export_ready       │
 │                                       │
 │  4. <iframe :srcdoc="html" />         │  (渲染报告)
 │                                       │
 │  5. GET /api/reports/.../exports/pdf  │
 │  ─────────────────────────────────→   │
 │  ← 200 (PDF 文件流)                   │  (浏览器下载)
```

---

## 7. 错误处理建议

| 场景 | 前端处理 |
|------|---------|
| 文件上传 413/415 | 提示用户文件格式或大小不符合要求 |
| 创建报告 404（missing_file_ids） | 提示文件已过期，重新上传 |
| SSE 连接中断 | 自动重连（参考现有 `consumeReportSseWithReconnect`，最多 20 次，退避策略 400ms*n，上限 5s） |
| `artifact_error` 事件 | 展示错误信息，允许用户重试 |
| 导出 409 | 报告未完成，禁用导出按钮 |
| 导出 501 | 该格式不可用，隐藏对应导出选项 |

---

## 8. 需要前端确认的问题

1. **路由方案**：报告功能放在哪个页面？新建 `/reports` 路由，还是嵌入现有聊天页？
2. **SSE 复用**：你们现有的 `stream.ts` 里的 SSE 解析逻辑能否直接扩展，还是需要独立模块？
3. **API 前缀**：报告接口走哪个 axios 实例？`/api/v1` 还是新建 `/api/v3`？
4. **文件上传方式**：当前用 `octet-stream`（非 multipart），你们的上传组件能否适配？
