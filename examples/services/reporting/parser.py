"""File parsers for the report pipeline.

Each parser produces a :class:`ParsedFile` carrying:
- a bounded ``text_preview`` (always safe to embed in prompts)
- a pointer (``text_full_path``) to the full extracted text on disk
- ``sheets`` for tabular files, each with a bounded preview plus
  ``full_csv_path`` pointing at the complete sheet on disk

The split keeps prompts small while giving agents a deterministic
path to read the entire content when they need it.

Output layout (caller-supplied ``output_dir``)::

    <output_dir>/text.txt              # full extracted text (if any)
    <output_dir>/sheet_<n>.csv         # full sheet content (xlsx/csv)
    <output_dir>/tables.json           # extracted tables (pdf/docx)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

from examples.services.reporting.models import ParsedFile, ParsedSheet


logger = logging.getLogger(__name__)


PREVIEW_TEXT_CHARS = 6000
PREVIEW_ROWS = 20
PREVIEW_COLS = 30
SHEET_HARD_ROW_CAP = 200_000   # safety guard: refuse pathological sheets
SHEET_HARD_COL_CAP = 1_000


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_file(
    *,
    file_id: str,
    filename: str,
    path: Path,
    size_bytes: int,
    output_dir: Path | None = None,
    extension: str | None = None,
) -> ParsedFile:
    """Parse ``path`` and return a :class:`ParsedFile`.

    ``output_dir`` is where full-content artifacts (text.txt, sheet_*.csv,
    tables.json) are written. When omitted, the parser still produces the
    in-memory ParsedFile but skips writing companion files; downstream code
    that wants full-content access must provide a directory.

    ``extension`` lets the caller dispatch by extension explicitly. This
    matters when ``path`` points at a content-addressed blob whose disk
    name has no suffix; in that case the original ``filename`` carries
    the extension and we cannot rely on ``path.suffix``.
    """
    if extension is None:
        extension = Path(filename).suffix.lower() or path.suffix.lower()
    extension = extension.lower()
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if extension == ".csv":
            return _parse_csv(file_id, filename, extension, size_bytes, path, output_dir)
        if extension == ".xlsx":
            return _parse_xlsx(file_id, filename, extension, size_bytes, path, output_dir)
        if extension == ".pdf":
            return _parse_pdf(file_id, filename, extension, size_bytes, path, output_dir)
        if extension == ".docx":
            return _parse_docx(file_id, filename, extension, size_bytes, path, output_dir)
        if extension == ".pptx":
            return _parse_pptx(file_id, filename, extension, size_bytes, path, output_dir)
        if extension in {".md", ".markdown", ".txt"}:
            return _parse_text(file_id, filename, extension, size_bytes, path, output_dir)
        if extension in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            return _parse_image(file_id, filename, extension, size_bytes, path)
    except Exception as exc:  # parser must never crash the upload path
        logger.exception("parser_failed file_id=%s ext=%s", file_id, extension)
        return ParsedFile(
            file_id=file_id,
            filename=filename,
            extension=extension,
            size_bytes=size_bytes,
            kind="unknown",
            warnings=[f"parser crashed: {type(exc).__name__}: {exc}"],
        )

    return ParsedFile(
        file_id=file_id,
        filename=filename,
        extension=extension,
        size_bytes=size_bytes,
        kind="unknown",
        warnings=[f"no parser registered for extension: {extension or '(none)'}"],
    )


# ---------------------------------------------------------------------------
# Backwards-compatible shims (existing tests + callers).
# ---------------------------------------------------------------------------


def parse_financial_file(
    *,
    file_id: str,
    filename: str,
    path: Path,
    size_bytes: int,
    output_dir: Path | None = None,
) -> ParsedFile:
    return parse_file(
        file_id=file_id,
        filename=filename,
        path=path,
        size_bytes=size_bytes,
        output_dir=output_dir,
    )


def parse_attachment_metadata(
    *,
    file_id: str,
    filename: str,
    extension: str,
    size_bytes: int,
    path: Path | None = None,
    output_dir: Path | None = None,
) -> ParsedFile:
    if path is None:
        return ParsedFile(
            file_id=file_id,
            filename=filename,
            extension=extension,
            size_bytes=size_bytes,
            kind=_kind_for_extension(extension),
        )
    return parse_file(
        file_id=file_id,
        filename=filename,
        path=path,
        size_bytes=size_bytes,
        output_dir=output_dir,
    )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def _parse_csv(
    file_id: str,
    filename: str,
    extension: str,
    size_bytes: int,
    path: Path,
    output_dir: Path | None,
) -> ParsedFile:
    warnings: list[str] = []
    text = _read_text_with_fallback(path, warnings)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(text.splitlines(), dialect)
    rows = [_normalize_row(row) for row in reader]
    rows = [row for row in rows if any(cell for cell in row)]
    headers, preview_rows = _headers_and_preview(rows)

    sheet_name = path.stem or "CSV"
    full_csv_path = _write_sheet_csv(
        output_dir,
        sheet_index=1,
        rows=rows,
    )
    sheet = ParsedSheet(
        name=sheet_name,
        headers=headers,
        rows=preview_rows,
        row_count=max(0, len(rows) - (1 if headers else 0)),
        column_count=max((len(row) for row in rows), default=0),
        full_csv_path=full_csv_path,
    )
    return ParsedFile(
        file_id=file_id,
        filename=filename,
        extension=extension,
        size_bytes=size_bytes,
        kind="table",
        sheets=[sheet],
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# XLSX (full content via openpyxl)
# ---------------------------------------------------------------------------


def _parse_xlsx(
    file_id: str,
    filename: str,
    extension: str,
    size_bytes: int,
    path: Path,
    output_dir: Path | None,
) -> ParsedFile:
    warnings: list[str] = []
    try:
        from openpyxl import load_workbook  # type: ignore[import-not-found]
    except Exception as exc:
        warnings.append(f"openpyxl unavailable: {type(exc).__name__}")
        return ParsedFile(
            file_id=file_id,
            filename=filename,
            extension=extension,
            size_bytes=size_bytes,
            kind="table",
            warnings=warnings,
        )

    # ``openpyxl`` validates the file format by its filename extension
    # before it even opens the bytes. Our content-addressed blob path
    # has no suffix (e.g. ``data/uploads/blobs/ab/abcd...``), so passing
    # the path directly produces:
    #   "openpyxl does not support  file format, please check ..."
    # Handing it the open binary stream skips the extension sniff and
    # makes openpyxl read the zip container directly.
    fh = path.open("rb")
    sheets: list[ParsedSheet] = []
    try:
        workbook = load_workbook(filename=fh, read_only=True, data_only=True)
    except Exception:
        fh.close()
        raise
    try:
        for sheet_index, sheet_name in enumerate(workbook.sheetnames, start=1):
            ws = workbook[sheet_name]
            raw_rows: list[list[str]] = []
            for row in ws.iter_rows(values_only=True):
                if len(raw_rows) >= SHEET_HARD_ROW_CAP:
                    warnings.append(
                        f"sheet '{sheet_name}' truncated at {SHEET_HARD_ROW_CAP} rows"
                    )
                    break
                cells = [_format_cell(value) for value in row[:SHEET_HARD_COL_CAP]]
                if any(cell for cell in cells):
                    raw_rows.append(cells)
            headers, preview_rows = _headers_and_preview(raw_rows)
            full_csv_path = _write_sheet_csv(
                output_dir,
                sheet_index=sheet_index,
                rows=raw_rows,
            )
            sheets.append(
                ParsedSheet(
                    name=sheet_name,
                    headers=headers,
                    rows=preview_rows,
                    row_count=max(0, len(raw_rows) - (1 if headers else 0)),
                    column_count=max((len(row) for row in raw_rows), default=0),
                    full_csv_path=full_csv_path,
                )
            )
    finally:
        workbook.close()
        fh.close()

    if not sheets:
        warnings.append("no readable worksheets found")
    return ParsedFile(
        file_id=file_id,
        filename=filename,
        extension=extension,
        size_bytes=size_bytes,
        kind="table",
        sheets=sheets,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# PDF (all pages + tables via pdfplumber)
# ---------------------------------------------------------------------------


def _parse_pdf(
    file_id: str,
    filename: str,
    extension: str,
    size_bytes: int,
    path: Path,
    output_dir: Path | None,
) -> ParsedFile:
    warnings: list[str] = []
    try:
        import pdfplumber  # type: ignore[import-untyped]
    except Exception as exc:
        warnings.append(f"pdf text extraction unavailable: {type(exc).__name__}")
        return ParsedFile(
            file_id=file_id,
            filename=filename,
            extension=extension,
            size_bytes=size_bytes,
            kind="pdf",
            warnings=warnings,
        )

    page_chunks: list[str] = []
    tables: list[dict[str, Any]] = []
    page_count = 0
    try:
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            for index, page in enumerate(pdf.pages, start=1):
                text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
                if text:
                    page_chunks.append(f"# Page {index}\n{text.strip()}")
                try:
                    for table in page.extract_tables() or []:
                        if not table:
                            continue
                        tables.append({
                            "page": index,
                            "rows": [[_clean_cell(cell) for cell in row] for row in table],
                        })
                except Exception as exc:  # noqa: BLE001
                    warnings.append(
                        f"pdf table extraction failed on page {index}: {type(exc).__name__}"
                    )
    except Exception as exc:
        warnings.append(f"pdf text extraction failed: {type(exc).__name__}: {exc}")
        return ParsedFile(
            file_id=file_id,
            filename=filename,
            extension=extension,
            size_bytes=size_bytes,
            kind="pdf",
            page_count=page_count,
            warnings=warnings,
        )

    full_text = "\n\n".join(page_chunks).strip()
    if not full_text:
        warnings.append("pdf text could not be extracted; scanned PDF may require OCR")
    preview, full_path, total_chars = _persist_text(full_text, output_dir)
    if tables and output_dir is not None:
        _write_tables_json(output_dir, tables)

    return ParsedFile(
        file_id=file_id,
        filename=filename,
        extension=extension,
        size_bytes=size_bytes,
        kind="pdf",
        text_preview=preview,
        text_full_path=full_path,
        text_total_chars=total_chars,
        page_count=page_count,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def _parse_docx(
    file_id: str,
    filename: str,
    extension: str,
    size_bytes: int,
    path: Path,
    output_dir: Path | None,
) -> ParsedFile:
    warnings: list[str] = []
    try:
        from docx import Document  # type: ignore[import-not-found]
    except Exception as exc:
        warnings.append(f"python-docx unavailable: {type(exc).__name__}")
        return ParsedFile(
            file_id=file_id,
            filename=filename,
            extension=extension,
            size_bytes=size_bytes,
            kind="document",
            warnings=warnings,
        )

    document = Document(str(path))
    parts: list[str] = []
    for paragraph in document.paragraphs:
        text = (paragraph.text or "").strip()
        if not text:
            continue
        style = (paragraph.style.name or "").lower() if paragraph.style else ""
        if "heading 1" in style:
            parts.append(f"\n# {text}")
        elif "heading 2" in style:
            parts.append(f"\n## {text}")
        elif "heading 3" in style:
            parts.append(f"\n### {text}")
        else:
            parts.append(text)

    tables: list[dict[str, Any]] = []
    for table_index, table in enumerate(document.tables, start=1):
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        if rows:
            tables.append({"table_index": table_index, "rows": rows})
            parts.append("\n[table]")
            for row in rows[:50]:
                parts.append(" | ".join(row))

    full_text = "\n".join(parts).strip()
    preview, full_path, total_chars = _persist_text(full_text, output_dir)
    if tables and output_dir is not None:
        _write_tables_json(output_dir, tables)

    return ParsedFile(
        file_id=file_id,
        filename=filename,
        extension=extension,
        size_bytes=size_bytes,
        kind="document",
        text_preview=preview,
        text_full_path=full_path,
        text_total_chars=total_chars,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------


def _parse_pptx(
    file_id: str,
    filename: str,
    extension: str,
    size_bytes: int,
    path: Path,
    output_dir: Path | None,
) -> ParsedFile:
    warnings: list[str] = []
    try:
        from pptx import Presentation  # type: ignore[import-not-found]
    except Exception as exc:
        warnings.append(f"python-pptx unavailable: {type(exc).__name__}")
        return ParsedFile(
            file_id=file_id,
            filename=filename,
            extension=extension,
            size_bytes=size_bytes,
            kind="slides",
            warnings=warnings,
        )

    presentation = Presentation(str(path))
    parts: list[str] = []
    slide_count = 0
    for index, slide in enumerate(presentation.slides, start=1):
        slide_count = index
        parts.append(f"\n# Slide {index}")
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for paragraph in shape.text_frame.paragraphs:
                text = "".join(run.text or "" for run in paragraph.runs).strip()
                if text:
                    parts.append(text)
        notes = slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""
        notes = (notes or "").strip()
        if notes:
            parts.append(f"[notes] {notes}")

    full_text = "\n".join(parts).strip()
    preview, full_path, total_chars = _persist_text(full_text, output_dir)

    return ParsedFile(
        file_id=file_id,
        filename=filename,
        extension=extension,
        size_bytes=size_bytes,
        kind="slides",
        text_preview=preview,
        text_full_path=full_path,
        text_total_chars=total_chars,
        page_count=slide_count,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Markdown / plain text
# ---------------------------------------------------------------------------


def _parse_text(
    file_id: str,
    filename: str,
    extension: str,
    size_bytes: int,
    path: Path,
    output_dir: Path | None,
) -> ParsedFile:
    warnings: list[str] = []
    text = _read_text_with_fallback(path, warnings)
    preview, full_path, total_chars = _persist_text(text.strip(), output_dir)
    return ParsedFile(
        file_id=file_id,
        filename=filename,
        extension=extension,
        size_bytes=size_bytes,
        kind="text",
        text_preview=preview,
        text_full_path=full_path,
        text_total_chars=total_chars,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def _parse_image(
    file_id: str,
    filename: str,
    extension: str,
    size_bytes: int,
    path: Path,
) -> ParsedFile:
    warnings: list[str] = []
    width = height = 0
    try:
        from PIL import Image  # type: ignore[import-not-found]

        with Image.open(path) as image:
            width, height = image.size
    except Exception as exc:
        warnings.append(f"image metadata extraction failed: {type(exc).__name__}")

    summary = f"image {width}x{height}" if width and height else "image (size unknown)"
    return ParsedFile(
        file_id=file_id,
        filename=filename,
        extension=extension,
        size_bytes=size_bytes,
        kind="image",
        text_preview=summary,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _persist_text(
    text: str,
    output_dir: Path | None,
) -> tuple[str, str, int]:
    """Return (preview, full_path, total_chars).

    The preview is always safe to embed in prompts. ``full_path`` is set when
    the full content is longer than the preview and we managed to write it
    to disk.
    """
    total = len(text)
    if not text:
        return "", "", 0
    preview = text[:PREVIEW_TEXT_CHARS]
    if total <= PREVIEW_TEXT_CHARS or output_dir is None:
        return preview, "", total
    full_path = output_dir / "text.txt"
    full_path.write_text(text, encoding="utf-8")
    return preview, _relative_to_root(full_path), total


def _write_sheet_csv(
    output_dir: Path | None,
    *,
    sheet_index: int,
    rows: list[list[str]],
) -> str:
    if output_dir is None or not rows:
        return ""
    path = output_dir / f"sheet_{sheet_index}.csv"
    # ``newline=""`` is required so the csv writer's own \r\n line
    # terminators aren't doubled by the platform translation layer
    # (otherwise on Windows we'd write \r\r\n and readers see a blank
    # row between every real row).
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow(row)
    return _relative_to_root(path)


def _write_tables_json(output_dir: Path, tables: list[dict[str, Any]]) -> None:
    (output_dir / "tables.json").write_text(
        json.dumps(tables, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _relative_to_root(path: Path) -> str:
    """Best-effort project-root relative path; falls back to absolute string."""
    try:
        root = Path(__file__).resolve().parents[3]
        return str(path.resolve().relative_to(root))
    except (ValueError, OSError):
        return str(path)


def _read_text_with_fallback(path: Path, warnings: list[str]) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    warnings.append("file encoding could not be detected; latin-1 fallback was used")
    return data.decode("latin-1", errors="replace")


def _headers_and_preview(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    if not rows:
        return [], []
    header_index = _detect_header_row(rows)
    headers = rows[header_index][:PREVIEW_COLS]
    body = rows[header_index + 1 : header_index + 1 + PREVIEW_ROWS]
    width = min(max(len(headers), max((len(row) for row in body), default=0)), PREVIEW_COLS)
    return (
        _pad(headers, width),
        [_pad(row, width) for row in body],
    )


def _detect_header_row(rows: list[list[str]]) -> int:
    for index, row in enumerate(rows[:10]):
        non_empty = [cell for cell in row if cell]
        if not non_empty:
            continue
        text_like = sum(1 for cell in non_empty if _looks_like_header(cell))
        if text_like / len(non_empty) >= 0.55:
            return index
    return 0


def _looks_like_header(value: str) -> bool:
    compact = value.strip()
    if not compact:
        return False
    if re.fullmatch(r"[-+]?[\d,]+(\.\d+)?%?", compact):
        return False
    return True


def _normalize_row(row: list[str]) -> list[str]:
    return [cell.strip() for cell in row[:PREVIEW_COLS]]


def _pad(row: list[str], width: int) -> list[str]:
    return [row[i] if i < len(row) else "" for i in range(width)]


def _format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value).strip()


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _kind_for_extension(extension: str) -> str:
    ext = extension.lower()
    if ext in {".csv", ".xlsx", ".xls"}:
        return "table"
    if ext == ".pdf":
        return "pdf"
    if ext in {".docx", ".doc"}:
        return "document"
    if ext in {".pptx", ".ppt"}:
        return "slides"
    if ext in {".md", ".markdown", ".txt"}:
        return "text"
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return "image"
    return "unknown"
