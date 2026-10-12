"""Explicit graph runtime configuration and independent worker bootstrap."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


def load_worker_environment(path: Path | None = None) -> None:
    load_dotenv(path or Path(__file__).resolve().parents[4] / ".env", override=False)


@dataclass(frozen=True)
class GraphSettings:
    enabled: bool
    uri: str
    database: str
    read_user: str
    read_password: str = field(repr=False)
    write_user: str
    write_password: str = field(repr=False)
    event_secret: str = field(repr=False)
    source_instance: str
    project_mapping: dict[str, str]
    snapshot_url: str
    snapshot_token: str = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GraphSettings:
        env = os.environ if env is None else env
        mapping = json.loads(env.get("GRAPH_PROJECT_MAPPING", "{}"))
        if not isinstance(mapping, dict) or not all(
            isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
            for k, v in mapping.items()
        ):
            raise ValueError("GRAPH_PROJECT_MAPPING must map source projects to internal projects")
        value = cls(
            enabled=env.get("GRAPH_SYNC_ENABLED", "false").lower() == "true",
            uri=env.get("NEO4J_URI", "bolt://127.0.0.1:7687"),
            database=env.get("NEO4J_DATABASE", "neo4j"),
            read_user=env.get("NEO4J_READ_USER", ""),
            read_password=env.get("NEO4J_READ_PASSWORD", ""),
            write_user=env.get("NEO4J_WRITE_USER", ""),
            write_password=env.get("NEO4J_WRITE_PASSWORD", ""),
            event_secret=env.get("GRAPH_EVENT_SECRET", ""),
            source_instance=env.get("GRAPH_SOURCE_INSTANCE", "zentao"),
            project_mapping=mapping,
            snapshot_url=env.get("GRAPH_SNAPSHOT_URL", ""),
            snapshot_token=env.get("GRAPH_SNAPSHOT_TOKEN", ""),
        )
        if value.enabled and (not all((value.read_user, value.read_password,
                                      value.write_user, value.write_password))
                              or value.read_user == value.write_user):
            raise ValueError("Enabled graph runtime requires separate read/write credentials")
        if value.event_secret and not value.project_mapping:
            raise ValueError("Signed graph ingress requires an explicit project mapping")
        if value.snapshot_url:
            parsed = urlparse(value.snapshot_url)
            local_dev = env.get("APP_ENV", "development") == "development" and parsed.hostname in {"localhost", "127.0.0.1"}
            if not parsed.hostname or parsed.username or parsed.password or (
                parsed.scheme != "https" and not (parsed.scheme == "http" and local_dev)
            ):
                raise ValueError("Snapshot gateway requires HTTPS (localhost HTTP only in development)")
            if not value.snapshot_token or not value.project_mapping:
                raise ValueError("Snapshot gateway requires token and project mapping")
        return value


async def run_worker() -> None:
    import httpx
    from neo4j import AsyncGraphDatabase
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    from testmind.infrastructure.graph_sync.maintenance import (
        ActiveGenerationProjection,
        GraphMaintenance,
    )
    from testmind.infrastructure.graph_sync.polling import GraphPoller
    from testmind.infrastructure.graph_sync.projection import Neo4jProjection
    from testmind.infrastructure.graph_sync.source import SnapshotGraphSource
    from testmind.infrastructure.postgres.graph_sync_repository import GraphSyncRepository
    from testmind.worker.graph_sync_worker import GraphSyncWorker

    load_worker_environment()
    settings = GraphSettings.from_env()
    dsn = os.getenv("POSTGRES_DSN")
    if not settings.enabled or not dsn:
        raise RuntimeError("Enable GRAPH_SYNC_ENABLED and configure POSTGRES_DSN")
    async with AsyncConnectionPool(dsn, open=False, kwargs={
        "autocommit": True, "row_factory": dict_row, "prepare_threshold": 0,
    }) as pool, AsyncGraphDatabase.driver(settings.uri, auth=(
            settings.write_user, settings.write_password
        )) as driver, httpx.AsyncClient(timeout=30, follow_redirects=False) as http:
        projection = Neo4jProjection(driver, database=settings.database)
        await projection.ensure_constraints()
        repository = GraphSyncRepository(pool)
        def factory(generation: str) -> Neo4jProjection:
            return Neo4jProjection(driver, database=settings.database, generation=generation)
        worker = GraphSyncWorker(
            repository, ActiveGenerationProjection(repository, factory),
            maintenance=GraphMaintenance(repository, factory),
            poller=GraphPoller(repository, SnapshotGraphSource(
                http, url=settings.snapshot_url, token=settings.snapshot_token,
                source_instance=settings.source_instance, project_mapping=settings.project_mapping,
            ), source_instance=settings.source_instance) if settings.snapshot_url else None,
        )
        previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda *_: worker.stop())
        try:
            await worker.run()
        finally:
            signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    if os.name == "nt":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(run_worker())
    else:
        asyncio.run(run_worker())
