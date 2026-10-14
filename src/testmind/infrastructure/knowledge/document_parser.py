"""Strict extraction for supported knowledge document formats."""

from __future__ import annotations

import asyncio
import multiprocessing
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Any, cast

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


def parse_document(data: bytes, media_type: str, *, filename: str, max_bytes: int = 25 * 1024 * 1024, max_text_chars: int = 5_000_000, max_pages: int = 500) -> ParsedDocument:
    started = time.monotonic()
    if len(data) > max_bytes:
        raise ValueError("source_size_exceeded")
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    normalized_type = media_type.split(";", 1)[0].strip().lower()
    if suffix == "pdf" and normalized_type == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("File signature does not match PDF media type")
        try:
            reader = PdfReader(BytesIO(data))
            if len(reader.pages) > max_pages:
                raise ValueError("pdf_page_limit_exceeded")
            pages = []
            total = 0
            for page in reader.pages:
                extracted = page.extract_text() or ""
                total += len(extracted) + 1
                if total > max_text_chars:
                    raise ValueError("extracted_text_limit_exceeded")
                pages.append(extracted)
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
    if len(text) > max_text_chars:
        raise ValueError("extracted_text_limit_exceeded")
    return ParsedDocument(text, (0,), (), (), _elapsed(started))


def _parse_child(connection: Any, data: bytes, media_type: str, filename: str) -> None:
    try:
        connection.send((True, parse_document(data, media_type, filename=filename)))
    except Exception:  # noqa: BLE001 - isolate third-party parser failures in child.
        connection.send((False, "source_parse_failed"))
    finally:
        connection.close()


async def parse_document_bounded(data: bytes, media_type: str, *, filename: str, timeout_seconds: float = 30) -> ParsedDocument:
    if len(data) > 25 * 1024 * 1024:
        raise ValueError("source_size_exceeded")
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_parse_child, args=(sender, data, media_type, filename), daemon=True)
    process.start()
    sender.close()
    try:
        async with asyncio.timeout(timeout_seconds):
            while not receiver.poll():
                if not process.is_alive():
                    raise ValueError("source_parser_process_failed")
                await asyncio.sleep(.02)
            ok, parsed = receiver.recv()
            if not ok:
                raise ValueError("source_parse_failed")
            return cast(ParsedDocument, parsed)
    finally:
        if process.is_alive():
            process.terminate()
        await asyncio.to_thread(process.join, 5)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join)
        receiver.close()


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
