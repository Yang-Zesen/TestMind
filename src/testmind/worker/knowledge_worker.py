"""Independent PostgreSQL-leased worker for knowledge ingestion jobs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from pathlib import Path
import signal
from typing import Any
from uuid import uuid4

from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository

logger = logging.getLogger(__name__)
JobHandler = Callable[[Mapping[str, Any]], Awaitable[None]]


class RetryableJobError(Exception):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class PermanentJobError(Exception):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class KnowledgeWorker:
    def __init__(
        self,
        repository: KnowledgeRepository,
        *,
        handlers: Mapping[str, JobHandler],
        worker_id: str | None = None,
        lease_seconds: int = 60,
        heartbeat_seconds: int = 15,
        poll_seconds: float = 2.0,
    ) -> None:
        if lease_seconds <= 0 or not 0 < heartbeat_seconds < lease_seconds:
            raise ValueError("Heartbeat interval must be positive and shorter than the lease")
        self._repository = repository
        self._handlers = dict(handlers)
        self._worker_id = worker_id or str(uuid4())
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._poll_seconds = poll_seconds
        self._stopping = asyncio.Event()
        self._metrics = {"claimed": 0, "succeeded": 0, "retryable": 0, "permanent": 0}

    @property
    def metrics(self) -> dict[str, int]:
        return dict(self._metrics)

    def stop(self) -> None:
        """Stop claiming new jobs; an in-flight job is allowed to finish."""
        self._stopping.set()

    async def run(self) -> None:
        while not self._stopping.is_set():
            worked = await self.run_once()
            if not worked:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_seconds)
                except TimeoutError:
                    pass

    async def run_once(self) -> bool:
        await self._repository.finalize_expired_dead_letters()
        job = await self._repository.claim_next_job(
            worker_id=self._worker_id, lease_seconds=self._lease_seconds
        )
        if job is None:
            return False
        self._metrics["claimed"] += 1
        correlation_id = str(job.get("correlation_id", ""))
        job_id = str(job["id"])
        fields = {
            "job_id": job_id,
            "worker_id": self._worker_id,
            "correlation_id": correlation_id,
            "operation": str(job.get("operation", "")),
        }
        logger.info("knowledge_job_claimed", extra=fields)
        heartbeat_task = asyncio.create_task(self._heartbeat(job_id, fields))
        try:
            handler = self._handlers.get(str(job.get("operation", "")))
            if handler is None:
                raise PermanentJobError("unsupported_operation")
            await handler(job)
            completed = await self._repository.complete_job(
                job_id=job_id, worker_id=self._worker_id
            )
            if completed:
                self._metrics["succeeded"] += 1
                logger.info("knowledge_job_succeeded", extra=fields)
        except RetryableJobError as exc:
            await self._repository.schedule_retry(
                job_id=job_id, worker_id=self._worker_id, error_code=exc.error_code
            )
            self._metrics["retryable"] += 1
            logger.warning("knowledge_job_retryable_failure", extra={**fields, "error_code": exc.error_code})
        except PermanentJobError as exc:
            await self._repository.fail_permanently(
                job_id=job_id, worker_id=self._worker_id, error_code=exc.error_code
            )
            self._metrics["permanent"] += 1
            logger.error("knowledge_job_permanent_failure", extra={**fields, "error_code": exc.error_code})
        except asyncio.CancelledError:
            logger.warning("knowledge_job_interrupted_lease_will_expire", extra=fields)
            raise
        except Exception:
            await self._repository.schedule_retry(
                job_id=job_id, worker_id=self._worker_id, error_code="unexpected_worker_error"
            )
            self._metrics["retryable"] += 1
            logger.error(
                "knowledge_job_unexpected_failure",
                extra={**fields, "error_code": "unexpected_worker_error"},
            )
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        return True

    async def _heartbeat(self, job_id: str, fields: dict[str, str]) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            renewed = await self._repository.heartbeat(
                job_id=job_id, worker_id=self._worker_id, lease_seconds=self._lease_seconds
            )
            if not renewed:
                logger.error("knowledge_job_lease_lost", extra=fields)
                return


async def _run_configured_worker() -> None:
    from langchain_core.runnables.config import ensure_config
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    from testmind.core.config import get_config
    from testmind.infrastructure.knowledge.activation import KnowledgeActivationService
    from testmind.infrastructure.knowledge.ingestion import KnowledgeIngestionHandler
    from testmind.infrastructure.knowledge.milvus_index import MilvusKnowledgeIndex
    from testmind.infrastructure.knowledge.source_storage import create_source_storage

    del ensure_config
    config = get_config()
    if not config.postgres_dsn:
        raise RuntimeError("POSTGRES_DSN is required for the knowledge worker")
    if not Path(config.embedding_model).is_dir():
        raise RuntimeError("BGE_M3_PATH must reference an installed local model; downloads are disabled")

    from pymilvus import MilvusClient
    from pymilvus.model.hybrid import BGEM3EmbeddingFunction

    embedder = BGEM3EmbeddingFunction(
        model_name=config.embedding_model,
        device=config.embedding_device,
        use_fp16=config.embedding_fp16,
    )
    milvus_client = MilvusClient(
        uri=config.milvus_uri,
        token=config.milvus_token,
        timeout=config.milvus_timeout_seconds,
    )
    index = MilvusKnowledgeIndex(
        milvus_client,
        collection_name=config.milvus_collection,
        timeout_seconds=config.milvus_timeout_seconds,
    )
    storage = create_source_storage(config)
    model_version = Path(config.embedding_model).name

    async with AsyncConnectionPool(
        conninfo=config.postgres_dsn,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        repository = KnowledgeRepository(pool)
        ingestion = KnowledgeIngestionHandler(
            repository, storage, embedder, index, model_version=model_version
        )
        activation = KnowledgeActivationService(repository, index)

        async def activate(job: Mapping[str, Any]) -> None:
            await activation.activate(str(job["correlation_id"]))

        worker = KnowledgeWorker(
            repository,
            handlers={"ingest": ingestion, "reindex": ingestion, "activate": activate},
            lease_seconds=60,
            heartbeat_seconds=15,
        )
        loop = asyncio.get_running_loop()
        previous_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(worker.stop))
        try:
            await worker.run()
        finally:
            signal.signal(signal.SIGINT, previous_handler)
            with suppress(Exception):
                await asyncio.to_thread(milvus_client.close)
            logger.info("knowledge_worker_stopped", extra={"metrics": worker.metrics})


def main() -> None:
    asyncio.run(_run_configured_worker())


if __name__ == "__main__":
    main()
