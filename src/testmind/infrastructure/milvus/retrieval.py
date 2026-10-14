"""Local BGE-M3 dense/sparse retrieval followed by local BGE reranking."""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Iterable
from pathlib import Path
from threading import Lock
from typing import Any, Protocol, cast

from pydantic import ValidationError

from testmind.core.config import Config
from testmind.models.retrieval import KnowledgeChunk, RetrievalScope


class Retriever(Protocol):
    async def retrieve(
        self,
        query: str,
        scope: RetrievalScope,
        *,
        limit: int = 5,
    ) -> list[KnowledgeChunk]: ...


class MilvusRetrievalService:
    """Filter both recall channels, fuse ranks, then score with a local reranker."""

    def __init__(
        self, config: Config, *, client: Any = None, encoder: Any = None, reranker: Any = None
    ) -> None:
        self._config = config
        self._client = client
        self._encoder = encoder
        self._reranker = reranker
        self._init_lock = Lock()
        self._inference_lock = Lock()

    @staticmethod
    def build_filter(scope: RetrievalScope) -> str:
        if not scope.active_versions and not scope.document_versions:
            return ""
        if scope.active_versions:
            versions = " or ".join(
                f"(document_id == {json.dumps(document_id, ensure_ascii=False)} and "
                f"document_version == {json.dumps(version.document_version, ensure_ascii=False)} and "
                f"index_version == {json.dumps(version.index_version, ensure_ascii=False)})"
                for document_id, version in sorted(scope.active_versions.items())
            )
        else:
            versions = " or ".join(
                f"(document_id == {json.dumps(document_id, ensure_ascii=False)} and "
                f"document_version == {json.dumps(version, ensure_ascii=False)})"
                for document_id, version in sorted(scope.document_versions.items())
            )
        clauses = [
            f"project_id == {json.dumps(scope.project_id, ensure_ascii=False)}",
            "is_active == true",
            f"({versions})",
        ]
        if scope.knowledge_base_ids:
            kb_values = ", ".join(
                json.dumps(value, ensure_ascii=False)
                for value in sorted(scope.knowledge_base_ids)
            )
            clauses.append(f"knowledge_base_id in [{kb_values}]")
        return " and ".join(clauses)

    async def retrieve(
        self, query: str, scope: RetrievalScope, *, limit: int = 5
    ) -> list[KnowledgeChunk]:
        if not query.strip() or not (scope.active_versions or scope.document_versions):
            return []
        if not 1 <= limit <= 20:
            raise ValueError("Retrieval limit must be between 1 and 20")
        await asyncio.to_thread(self._initialize)
        filter_expression = self.build_filter(scope)
        candidate_limit = min(limit * 4, 80)
        hits = await asyncio.to_thread(
            self._hybrid_search, query, filter_expression, candidate_limit
        )
        candidates = self._validated_candidates(hits, scope)
        if not candidates:
            return []
        pairs = [[query, candidate.content[:4000]] for candidate in candidates]
        scores = await asyncio.to_thread(self._score, pairs)
        ranked = [
            KnowledgeChunk.model_validate({**candidate.model_dump(), "relevance": float(score)})
            for candidate, score in zip(candidates, scores, strict=True)
        ]
        return sorted(ranked, key=lambda candidate: candidate.relevance, reverse=True)[:limit]

    def _score(self, pairs: list[list[str]]) -> list[float]:
        with self._inference_lock:
            scores = self._reranker.compute_score(pairs, normalize=True)
        return [float(scores)] if isinstance(scores, (float, int)) else list(scores)

    @staticmethod
    def _validated_candidates(hits: Iterable[Any], scope: RetrievalScope) -> list[KnowledgeChunk]:
        deduplicated: dict[str, KnowledgeChunk] = {}
        for raw_hit in hits:
            if not isinstance(raw_hit, dict):
                continue
            entity = raw_hit.get("entity", {})
            if not isinstance(entity, dict) or entity.get("is_active") is not True:
                continue
            if entity.get("project_id") != scope.project_id:
                continue
            if scope.knowledge_base_ids and entity.get("knowledge_base_id") not in scope.knowledge_base_ids:
                continue
            document_id = entity.get("document_id")
            if not isinstance(document_id, str):
                continue
            active = scope.active_versions.get(document_id)
            if active:
                if (
                    active.document_version != entity.get("document_version")
                    or active.index_version != entity.get("index_version")
                ):
                    continue
            elif scope.document_versions.get(document_id) != entity.get("document_version"):
                continue
            if (
                not isinstance(entity.get("content"), str)
                or not entity["content"].strip()
                or not entity.get("source_locator")
            ):
                continue
            try:
                candidate = KnowledgeChunk.model_validate(
                    {
                        **entity,
                        "chunk_id": entity.get("chunk_id", raw_hit.get("id")),
                        "relevance": 0.0,
                    }
                )
            except ValidationError:
                continue
            deduplicated.setdefault(candidate.chunk_id, candidate)
        return list(deduplicated.values())

    def _initialize(self) -> None:
        with self._init_lock:
            if self._encoder is None or self._reranker is None:
                torch = importlib.import_module("torch")
                torch.set_num_threads(4)
                # Loading must stay offline; setup is an explicit operator action.
                for configured in (self._config.embedding_model, self._config.reranker_model):
                    if not configured or not Path(configured).is_dir():
                        raise RuntimeError(
                            "Local embedding/reranker model directory is not configured"
                        )
                if self._encoder is None:
                    BGEM3EmbeddingFunction = importlib.import_module("pymilvus.model.hybrid").BGEM3EmbeddingFunction

                    self._encoder = BGEM3EmbeddingFunction(
                        model_name=self._config.embedding_model,
                        device=self._config.embedding_device,
                        use_fp16=self._config.embedding_fp16,
                    )
                if self._reranker is None:
                    FlagReranker = importlib.import_module("FlagEmbedding").FlagReranker

                    self._reranker = FlagReranker(
                        model_name_or_path=self._config.reranker_model,
                        devices=self._config.reranker_device,
                        use_fp16=self._config.reranker_fp16,
                    )
            if self._client is None:
                MilvusClient = importlib.import_module("pymilvus").MilvusClient

                self._client = MilvusClient(
                    uri=self._config.milvus_uri,
                    token=self._config.milvus_token,
                    timeout=self._config.milvus_timeout_seconds,
                )

    async def aclose(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)

    def _hybrid_search(self, query: str, expression: str, limit: int) -> list[dict[str, Any]]:
        milvus = importlib.import_module("pymilvus")
        AnnSearchRequest, WeightedRanker = milvus.AnnSearchRequest, milvus.WeightedRanker

        with self._inference_lock:
            embeddings = self._encoder.encode_queries([query])
        dense_vector = embeddings["dense"][0]
        if hasattr(dense_vector, "tolist"):
            dense_vector = dense_vector.tolist()
        sparse = embeddings["sparse"]
        sparse_vector = self._sparse_row_to_dict(sparse, 0)
        requests = [
            AnnSearchRequest(
                data=[dense_vector],
                anns_field=self._config.milvus_dense_field,
                param={"metric_type": self._config.milvus_dense_metric},
                expr=expression or None,
                limit=limit,
            ),
            AnnSearchRequest(
                data=[sparse_vector],
                anns_field=self._config.milvus_sparse_field,
                param={"metric_type": "IP"},
                expr=expression or None,
                limit=limit,
            ),
        ]
        result = self._client.hybrid_search(
            collection_name=self._config.milvus_collection,
            reqs=requests,
            ranker=WeightedRanker(0.5, 0.5, norm_score=True),
            limit=limit,
            output_fields=[
                "chunk_id",
                "document_id",
                "document_version",
                "index_version",
                "project_id",
                "knowledge_base_id",
                "acl_version",
                "title",
                "content",
                "source_locator",
                "page",
                "section",
                "is_active",
            ],
            timeout=self._config.milvus_timeout_seconds,
        )
        return cast(list[dict[str, Any]], list(result[0])) if result else []

    @staticmethod
    def _sparse_row_to_dict(matrix: Any, row: int) -> dict[int, float]:
        if isinstance(matrix, list) and matrix and isinstance(matrix[0], dict):
            return {int(key): float(value) for key, value in matrix[row].items()}
        start, end = matrix.indptr[row : row + 2]
        return {
            int(key): float(value)
            for key, value in zip(matrix.indices[start:end], matrix.data[start:end], strict=True)
        }
