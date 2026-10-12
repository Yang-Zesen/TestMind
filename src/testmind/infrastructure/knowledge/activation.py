"""Recoverable PG-authoritative activation across PostgreSQL and Milvus."""

from __future__ import annotations

import asyncio
from typing import Any

from testmind.infrastructure.knowledge.milvus_index import MilvusKnowledgeIndex
from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository


class ActivationNotReady(RuntimeError):
    """Milvus state is not ready for the authoritative PG pointer switch."""


class KnowledgeActivationService:
    def __init__(self, repository: KnowledgeRepository, index: MilvusKnowledgeIndex) -> None:
        self._repository = repository
        self._index = index

    async def activate(self, operation_id: str) -> None:
        operation = await self._repository.get_activation_operation(operation_id)
        if operation is None:
            raise ActivationNotReady("activation_operation_missing")
        target_count = _count(operation.get("target_chunk_count"))
        changed = await asyncio.to_thread(
            self._index.set_version_active,
            project_id=operation["project_id"],
            kb_id=operation["kb_id"],
            document_id=operation["document_id"],
            document_version=operation["to_document_version"],
            index_version=operation["to_index_version"],
            active=True,
        )
        if target_count < 1 or changed != target_count:
            raise ActivationNotReady("target_milvus_version_not_fully_active")

        await self._repository.switch_active_version(operation_id)

        old_version = operation.get("from_document_version")
        old_index = operation.get("from_index_version")
        if old_version and old_index:
            expected_old_count = _count(operation.get("old_chunk_count"))
            deactivated = await asyncio.to_thread(
                self._index.set_version_active,
                project_id=operation["project_id"],
                kb_id=operation["kb_id"],
                document_id=operation["document_id"],
                document_version=old_version,
                index_version=old_index,
                active=False,
            )
            if deactivated != expected_old_count:
                raise ActivationNotReady("old_milvus_version_cleanup_incomplete")
        await self._repository.finish_activation(operation_id)


def _count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
