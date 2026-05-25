from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class PdfRendererUnavailable(RuntimeError):
    """Raised when no local browser renderer can produce a PDF."""


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
        html_path.write_text(html, encoding="utf-8")

        errors: list[str] = []
        for headless_flag in ("--headless=new", "--headless"):
            command = [
                browser,
                headless_flag,
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
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
