"""Strict extraction for supported knowledge document formats."""

from __future__ import annotations

import time
from dataclasses import dataclass

from pypdf import PdfReader


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    text: str
    page_offsets: tuple[int, ...]
    image_refs: tuple[str, ...]
    warnings: tuple[str, ...]
    parse_duration_ms: int


def validate_upload(data: bytes, media_type: str, *, filename: str) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    normalized_type = media_type.split(";", 1)[0].strip().lower()
    expected = {"md": "text/markdown", "markdown": "text/markdown", "txt": "text/plain"}
    if suffix == "pdf" and normalized_type == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("File signature does not match PDF media type")
        return suffix
    if suffix not in expected or normalized_type != expected[suffix]:
        raise ValueError("Only matching PDF, Markdown, and TXT files are supported")
    if data.startswith(b"%PDF-") or b"\x00" in data:
        raise ValueError("Text file signature does not match its media type")
    try:
        data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("Text document must be valid UTF-8") from exc
    return suffix


def parse_document(data: bytes, media_type: str, *, filename: str) -> ParsedDocument:
    started = time.monotonic()
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    normalized_type = media_type.split(";", 1)[0].strip().lower()
    if suffix == "pdf" and normalized_type == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("File signature does not match PDF media type")
        try:
            pages = [page.extract_text() or "" for page in PdfReader(data).pages]
        except Exception as exc:
            raise ValueError("PDF could not be parsed") from exc
        offsets: list[int] = []
        parts: list[str] = []
        cursor = 0
        for page_text in pages:
            offsets.append(cursor)
            parts.append(page_text)
            cursor += len(page_text) + 1
        text = "\n".join(parts)
        warnings = ("pdf_text_empty_possible_scanned_document",) if not text.strip() else ()
        return ParsedDocument(text, tuple(offsets), (), warnings, _elapsed(started))

    validate_upload(data, normalized_type, filename=filename)
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("Text document must be valid UTF-8") from exc
    return ParsedDocument(text, (0,), (), (), _elapsed(started))


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
