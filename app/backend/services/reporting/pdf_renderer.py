from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class PdfRendererUnavailable(RuntimeError):
    """Raised when no local browser renderer can produce a PDF."""


PRINT_CSS = """
<style id="agentengine-export-print-css">
@page {
  size: A4;
  margin: 15mm 14mm 16mm;
}

html,
body {
  width: auto !important;
  min-width: 0 !important;
  min-height: 0 !important;
  margin: 0 !important;
  background: #ffffff !important;
  color: #172033 !important;
  font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", Arial, sans-serif !important;
  font-size: 10.5pt !important;
  line-height: 1.55 !important;
  -webkit-print-color-adjust: exact !important;
  print-color-adjust: exact !important;
}

* {
  box-sizing: border-box !important;
  max-width: 100% !important;
}

main,
.container,
.report,
.report-container,
.dashboard,
.page,
.paper,
.content,
.wrapper {
  width: auto !important;
  max-width: none !important;
  min-height: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  background: #ffffff !important;
  box-shadow: none !important;
}

section,
article,
figure,
table,
pre,
blockquote,
.card,
.panel,
.kpi,
.kpi-card,
.metric-card,
.chart,
.chart-card,
.table-card,
.callout {
  break-inside: avoid !important;
  page-break-inside: avoid !important;
}

h1,
h2,
h3,
h4 {
  break-after: avoid !important;
  page-break-after: avoid !important;
  margin-top: 0 !important;
}

p,
li {
  orphans: 3;
  widows: 3;
}

table {
  width: 100% !important;
  border-collapse: collapse !important;
  font-size: 9pt !important;
}

thead {
  display: table-header-group !important;
}

tfoot {
  display: table-footer-group !important;
}

tr,
td,
th {
  break-inside: avoid !important;
  page-break-inside: avoid !important;
}

td,
th {
  padding: 6px 8px !important;
  border: 1px solid #d7dee8 !important;
  vertical-align: top !important;
}

img,
svg,
canvas {
  max-width: 100% !important;
  height: auto !important;
}

a {
  color: inherit !important;
  text-decoration: none !important;
}
</style>
"""


def render_pdf(html: str, *, timeout_seconds: int = 45) -> bytes:
    browser = _find_browser()
    if browser is None:
        raise PdfRendererUnavailable(
            "No Chrome or Edge executable was found. Install a Chromium browser or set REPORT_PDF_BROWSER."
        )

    with tempfile.TemporaryDirectory(prefix="agentengine-report-pdf-") as temp_dir:
        workdir = Path(temp_dir)
        html_path = workdir / "report.html"
        pdf_path = workdir / "report.pdf"
        html_path.write_text(prepare_print_html(html), encoding="utf-8")

        errors: list[str] = []
        for headless_flag in ("--headless=new", "--headless"):
            command = [
                browser,
                headless_flag,
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--no-first-run",
                "--no-default-browser-check",
                "--print-to-pdf-no-header",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=10000",
                f"--print-to-pdf={pdf_path}",
                html_path.as_uri(),
            ]
            try:
                result = subprocess.run(
                    command,
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                errors.append(f"timed out after {exc.timeout} seconds")
                continue
            if result.returncode == 0 and pdf_path.exists() and pdf_path.stat().st_size > 0:
                return pdf_path.read_bytes()
            errors.append((result.stderr or result.stdout or f"exit code {result.returncode}").strip())
            if pdf_path.exists():
                pdf_path.unlink()

    raise PdfRendererUnavailable("Chromium PDF export failed: " + " | ".join(error for error in errors if error))


def prepare_print_html(html: str) -> str:
    """Inject export-oriented print CSS into arbitrary model HTML."""

    if "agentengine-export-print-css" in html:
        return html

    lowered = html.lower()
    head_end = lowered.find("</head>")
    if head_end >= 0:
        return html[:head_end] + PRINT_CSS + html[head_end:]

    html_start_end = lowered.find(">")
    if lowered.lstrip().startswith("<!doctype") or "<html" in lowered:
        html_tag = lowered.find("<html")
        if html_tag >= 0:
            tag_end = lowered.find(">", html_tag)
            if tag_end >= 0:
                return html[: tag_end + 1] + "<head>" + PRINT_CSS + "</head>" + html[tag_end + 1 :]
        return "<head>" + PRINT_CSS + "</head>" + html

    if html_start_end >= 0:
        return f"<!doctype html><html><head>{PRINT_CSS}</head><body>{html}</body></html>"
    return f"<!doctype html><html><head>{PRINT_CSS}</head><body>{html}</body></html>"


def _find_browser() -> str | None:
    configured = os.getenv("REPORT_PDF_BROWSER", "").strip()
    if configured and Path(configured).exists():
        return configured
    if configured:
        found = shutil.which(configured)
        if found:
            return found

    for name in ("msedge", "msedge.exe", "chrome", "chrome.exe", "chromium", "chromium.exe", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found

    candidates = [
        Path(os.getenv("ProgramFiles", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.getenv("ProgramFiles", r"C:\Program Files")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.getenv("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None
