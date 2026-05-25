from __future__ import annotations

import csv
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from examples.services.reporting.models import ParsedFile, ParsedSheet


_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_MAX_PREVIEW_ROWS = 20
_MAX_PREVIEW_COLS = 30


def parse_attachment_metadata(
    *,
    file_id: str,
    filename: str,
    extension: str,
    size_bytes: int,
    path: Path | None = None,
) -> ParsedFile:
    """Build a minimal ParsedFile for non-tabular attachments (PDF, images)."""
    text_preview = ""
    page_count = 0
    warnings: list[str] = []
    if extension == ".pdf" and path is not None:
        text_preview, page_count, warnings = _parse_pdf_preview(path)
    return ParsedFile(
        file_id=file_id,
        filename=filename,
        extension=extension,
        size_bytes=size_bytes,
        sheets=[],
        text_preview=text_preview,
        page_count=page_count,
        warnings=warnings,
    )


def parse_financial_file(*, file_id: str, filename: str, path: Path, size_bytes: int) -> ParsedFile:
    extension = path.suffix.lower()
    if extension == ".csv":
        sheets, warnings = _parse_csv(path)
    elif extension == ".xlsx":
        sheets, warnings = _parse_xlsx(path)
    else:
        raise ValueError(f"unsupported file extension: {extension}")
    return ParsedFile(
        file_id=file_id,
        filename=filename,
        extension=extension,
        size_bytes=size_bytes,
        sheets=sheets,
        warnings=warnings,
    )


def _parse_csv(path: Path) -> tuple[list[ParsedSheet], list[str]]:
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
    sheet = ParsedSheet(
        name=path.stem or "CSV",
        headers=headers,
        rows=preview_rows,
        row_count=max(0, len(rows) - (1 if headers else 0)),
        column_count=max((len(row) for row in rows), default=0),
    )
    return [sheet], warnings


def _parse_xlsx(path: Path) -> tuple[list[ParsedSheet], list[str]]:
    warnings: list[str] = []
    sheets: list[ParsedSheet] = []
    with zipfile.ZipFile(path) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet_targets = _read_sheet_targets(archive)
        for sheet_name, target in sheet_targets:
            if target not in archive.namelist():
                warnings.append(f"sheet target not found: {target}")
                continue
            rows = _read_sheet_rows(archive, target, shared_strings)
            headers, preview_rows = _headers_and_preview(rows)
            sheets.append(
                ParsedSheet(
                    name=sheet_name,
                    headers=headers,
                    rows=preview_rows,
                    row_count=max(0, len(rows) - (1 if headers else 0)),
                    column_count=max((len(row) for row in rows), default=0),
                )
            )
    if not sheets:
        warnings.append("no readable worksheets found")
    return sheets, warnings


def _parse_pdf_preview(path: Path) -> tuple[str, int, list[str]]:
    warnings: list[str] = []
    try:
        import pdfplumber  # type: ignore[import-untyped]
    except Exception as exc:
        return "", 0, [f"pdf text extraction unavailable: {type(exc).__name__}"]

    chunks: list[str] = []
    page_count = 0
    try:
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages[:5]:
                text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    chunks.append(text)
    except Exception as exc:
        return "", page_count, [f"pdf text extraction failed: {type(exc).__name__}"]

    preview = "\n".join(chunks).strip()
    if not preview:
        warnings.append("pdf text could not be extracted; scanned PDF may require OCR")
    return preview[:6000], page_count, warnings


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
    headers = rows[header_index][:_MAX_PREVIEW_COLS]
    body = rows[header_index + 1:header_index + 1 + _MAX_PREVIEW_ROWS]
    width = min(max(len(headers), max((len(row) for row in body), default=0)), _MAX_PREVIEW_COLS)
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
    return [cell.strip() for cell in row[:_MAX_PREVIEW_COLS]]


def _pad(row: list[str], width: int) -> list[str]:
    return [row[i] if i < len(row) else "" for i in range(width)]


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall(f"{_NS_MAIN}si"):
        texts = [node.text or "" for node in item.iter(f"{_NS_MAIN}t")]
        values.append("".join(texts))
    return values


def _read_sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_by_id = {
        rel.attrib["Id"]: _normalize_xlsx_target(rel.attrib["Target"])
        for rel in rels.findall(f"{_NS_PKG_REL}Relationship")
        if "Id" in rel.attrib and "Target" in rel.attrib
    }
    out: list[tuple[str, str]] = []
    sheets_node = workbook.find(f"{_NS_MAIN}sheets")
    if sheets_node is None:
        return out
    for sheet in sheets_node.findall(f"{_NS_MAIN}sheet"):
        rel_id = sheet.attrib.get(f"{_NS_REL}id")
        target = rel_by_id.get(rel_id or "")
        if not target:
            continue
        out.append((sheet.attrib.get("name", "Sheet"), target))
    return out


def _normalize_xlsx_target(target: str) -> str:
    normalized = target.lstrip("/")
    if normalized.startswith("xl/"):
        return normalized
    return f"xl/{normalized}"


def _read_sheet_rows(
    archive: zipfile.ZipFile,
    target: str,
    shared_strings: list[str],
) -> list[list[str]]:
    root = ElementTree.fromstring(archive.read(target))
    rows: list[list[str]] = []
    for row_node in root.iter(f"{_NS_MAIN}row"):
        values: list[str] = []
        for cell in row_node.findall(f"{_NS_MAIN}c"):
            column_index = _column_index(cell.attrib.get("r", "")) or len(values)
            while len(values) < column_index:
                values.append("")
            values.append(_cell_value(cell, shared_strings))
        rows.append(values[:_MAX_PREVIEW_COLS])
    return [row for row in rows if any(cell for cell in row)]


def _cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        texts = [node.text or "" for node in cell.iter(f"{_NS_MAIN}t")]
        return "".join(texts).strip()
    value = cell.find(f"{_NS_MAIN}v")
    raw = (value.text or "").strip() if value is not None else ""
    if cell_type == "s":
        try:
            return shared_strings[int(raw)].strip()
        except (ValueError, IndexError):
            return raw
    return raw


def _column_index(reference: str) -> int | None:
    letters = "".join(ch for ch in reference if ch.isalpha())
    if not letters:
        return None
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1
