"""Idempotent document-to-Milvus ingestion handler."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from testmind.infrastructure.knowledge.chunking import KnowledgeChunkInput, split_document
from testmind.infrastructure.knowledge.document_parser import parse_document_bounded
from testmind.infrastructure.knowledge.milvus_index import MilvusKnowledgeIndex
from testmind.infrastructure.knowledge.quality import evaluate_quality
from testmind.infrastructure.knowledge.source_storage import SourceStorage
from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository
from testmind.worker.knowledge_worker import PermanentJobError, RetryableJobError


class DocumentEmbedder(Protocol):
    def encode_documents(self, texts: list[str]) -> Mapping[str, Any]: ...


class KnowledgeIngestionHandler:
    def __init__(
        self,
        repository: KnowledgeRepository,
        storage: SourceStorage,
        embedder: DocumentEmbedder,
        index: MilvusKnowledgeIndex,
        *,
        model_version: str,
        chunker_version: str = "char-1200-overlap-150-v1",
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._embedder = embedder
        self._index = index
        self._model_version = model_version
        self._chunker_version = chunker_version

    async def __call__(self, job: Mapping[str, Any]) -> None:
        inputs = await self._repository.get_version_processing_inputs(str(job["version_id"]))
        if inputs is None:
            # A deleted document intentionally becomes invisible to processing queries.
            async with self._repository._pool.connection() as connection:
                cursor = await connection.execute("SELECT d.tombstoned_at FROM knowledge.document_versions v JOIN knowledge.documents d ON d.id=v.document_id WHERE v.id=%s", (str(job["version_id"]),))
                deleted = await cursor.fetchone()
            if deleted and deleted["tombstoned_at"] is not None:
                return
            raise PermanentJobError("document_version_missing")
        if inputs.get("status") in {"ready", "active", "archived", "rejected"}:
            return
        try:
            with self._storage.open(inputs["storage_key"]) as source:
                content = source.read(25 * 1024 * 1024 + 1)
            suffix = Path(inputs["storage_key"]).suffix.lower()
            media_type = {
                ".pdf": "application/pdf",
                ".md": "text/markdown",
                ".markdown": "text/markdown",
                ".txt": "text/plain",
            }.get(suffix)
            if media_type is None:
                raise ValueError("unsupported source type")
            parsed = await parse_document_bounded(content, media_type, filename=f"source{suffix}")
        except (OSError, ValueError, TimeoutError):
            raise PermanentJobError("source_parse_failed") from None

        chunks = split_document(parsed, version_id=inputs["version_id"])
        report = evaluate_quality(parsed, chunks)
        await self._repository.save_quality_report(
            version_id=inputs["version_id"],
            report=report.model_dump(mode="json"),
            status="quality_passed" if report.quality_status == "passed" else "rejected",
        )
        if report.quality_status != "passed":
            return

        try:
            embeddings = await asyncio.to_thread(self._embedder.encode_documents, [chunk.content for chunk in chunks])
            dense = embeddings["dense"]
            sparse = embeddings["sparse"]
            records = [
                self._record(inputs, chunk, _vector_at(dense, index), _sparse_at(sparse, index))
                for index, chunk in enumerate(chunks)
            ]
            verified_count = await asyncio.to_thread(self._index.upsert_inactive, records)
        except Exception:  # noqa: BLE001 - classify provider/index failures without content.
            raise RetryableJobError("embedding_or_milvus_write_failed") from None

        accepted = verified_count == len(chunks)
        index_report = {
            "schema_version": 1,
            "index_status": "accepted" if accepted else "failed",
            "expected_chunk_count": len(chunks),
            "verified_chunk_count": verified_count,
            "model_version": self._model_version,
            "chunker_version": self._chunker_version,
        }
        await self._repository.save_index_report(
            version_id=inputs["version_id"], report=index_report, accepted=accepted
        )
        if not accepted:
            raise RetryableJobError("milvus_strong_consistency_verification_failed")

    def _record(
        self,
        inputs: Mapping[str, Any],
        chunk: KnowledgeChunkInput,
        dense: list[float],
        sparse: dict[int, float],
    ) -> dict[str, Any]:
        return {
            "chunk_id": chunk.chunk_id,
            "project_id": inputs["project_id"],
            "knowledge_base_id": inputs["kb_id"],
            "document_id": inputs["document_id"],
            "document_version": inputs["document_version"],
            "index_version": inputs["index_version"],
            "title": inputs["title"],
            "content": chunk.content,
            "source_locator": chunk.source_locator,
            "page": _page(chunk.source_locator),
            "section": None,
            "source_type": "manual",
            "acl_project_ids": [inputs["project_id"]],
            "acl_version": 1,
            "model_version": self._model_version,
            "chunker_version": self._chunker_version,
            "dense_vector": dense,
            "sparse_vector": sparse,
            "is_active": False,
        }


def _vector_at(values: Any, index: int) -> list[float]:
    row = values[index]
    return [float(value) for value in (row.tolist() if hasattr(row, "tolist") else row)]


def _sparse_at(values: Any, index: int) -> dict[int, float]:
    if isinstance(values, list) and values and isinstance(values[0], dict):
        return {int(key): float(value) for key, value in values[index].items()}
    start, end = values.indptr[index : index + 2]
    return {
        int(key): float(value)
        for key, value in zip(values.indices[start:end], values.data[start:end], strict=True)
    }


def _page(locator: str) -> int | None:
    if not locator.startswith("page:"):
        return None
    try:
        return int(locator.split(";", 1)[0].split(":", 1)[1])
    except ValueError:
        return None
