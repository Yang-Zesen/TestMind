"""Milvus writer with deterministic upserts and strong-consistency verification."""

from __future__ import annotations

import json
from typing import Any


class MilvusKnowledgeIndex:
    VECTOR_FIELDS = ("dense_vector", "sparse_vector")
    MUTABLE_FIELDS = (
        "chunk_id",
        "project_id",
        "knowledge_base_id",
        "document_id",
        "document_version",
        "index_version",
        "title",
        "content",
        "source_locator",
        "page",
        "section",
        "source_type",
        "acl_version",
        "model_version",
        "chunker_version",
        "dense_vector",
        "sparse_vector",
        "is_active",
    )

    def __init__(self, client: Any, *, collection_name: str, timeout_seconds: float = 10) -> None:
        self._client = client
        self._collection_name = collection_name
        self._timeout_seconds = timeout_seconds

    def upsert_inactive(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        inactive = [{**record, "is_active": False} for record in records]
        self._client.upsert(collection_name=self._collection_name, data=inactive, timeout=self._timeout_seconds)
        self._client.flush(collection_name=self._collection_name, timeout=self._timeout_seconds)
        ids = [record["chunk_id"] for record in inactive]
        found = self._client.query(
            collection_name=self._collection_name,
            filter=f"chunk_id in {json.dumps(ids)} and is_active == false",
            output_fields=["chunk_id", "is_active"],
            consistency_level="Strong",
            timeout=self._timeout_seconds,
        )
        return len({row["chunk_id"] for row in found if row.get("is_active") is False})

    def set_version_active(
        self,
        *,
        project_id: str,
        kb_id: str,
        document_id: str,
        document_version: str,
        index_version: str,
        active: bool,
    ) -> int:
        predicate = self._version_predicate(
            project_id, kb_id, document_id, document_version, index_version
        )
        rows = self._client.query(
            collection_name=self._collection_name,
            filter=predicate,
            output_fields=list(self.MUTABLE_FIELDS),
            consistency_level="Strong",
            timeout=self._timeout_seconds,
        )
        if not rows:
            return 0
        replacements = [{**row, "is_active": active} for row in rows]
        self._client.upsert(collection_name=self._collection_name, data=replacements, timeout=self._timeout_seconds)
        self._client.flush(collection_name=self._collection_name, timeout=self._timeout_seconds)
        expected_ids = {row["chunk_id"] for row in rows}
        verified = self._client.query(
            collection_name=self._collection_name,
            filter=f"{predicate} and is_active == {'true' if active else 'false'}",
            output_fields=["chunk_id", "is_active"],
            consistency_level="Strong",
            timeout=self._timeout_seconds,
        )
        verified_ids = {row["chunk_id"] for row in verified if row.get("is_active") is active}
        return len(expected_ids & verified_ids)

    @staticmethod
    def _version_predicate(
        project_id: str,
        kb_id: str,
        document_id: str,
        document_version: str,
        index_version: str,
    ) -> str:
        return " and ".join(
            f"{field} == {json.dumps(value, ensure_ascii=False)}"
            for field, value in (
                ("project_id", project_id),
                ("knowledge_base_id", kb_id),
                ("document_id", document_id),
                ("document_version", document_version),
                ("index_version", index_version),
            )
        )
