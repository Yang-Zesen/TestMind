"""Deterministic character-based chunking with stable per-version IDs."""

from __future__ import annotations

from dataclasses import dataclass

from testmind.infrastructure.knowledge.document_parser import ParsedDocument
from testmind.models.knowledge import deterministic_chunk_id


@dataclass(frozen=True, slots=True)
class KnowledgeChunkInput:
    chunk_id: str
    version_id: str
    chunk_index: int
    content: str
    source_locator: str
    is_active: bool = False


def split_document(
    parsed: ParsedDocument,
    *,
    version_id: str,
    max_chars: int = 1200,
    overlap_chars: int = 150,
) -> list[KnowledgeChunkInput]:
    if max_chars < 1 or overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("Chunk size must be positive and overlap smaller than chunk size")
    text = parsed.text.strip()
    if not text:
        return []
    chunks: list[KnowledgeChunkInput] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + max_chars // 2, end)
            if boundary > start:
                end = boundary
        content = text[start:end].strip()
        if content:
            chunks.append(
                KnowledgeChunkInput(
                    chunk_id=deterministic_chunk_id(version_id, len(chunks)),
                    version_id=version_id,
                    chunk_index=len(chunks),
                    content=content,
                    source_locator=_locator(parsed.page_offsets, start),
                )
            )
        if end == len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def _locator(page_offsets: tuple[int, ...], char_offset: int) -> str:
    page = 1
    for index, offset in enumerate(page_offsets):
        if offset > char_offset:
            break
        page = index + 1
    return f"page:{page};char:{char_offset}"
