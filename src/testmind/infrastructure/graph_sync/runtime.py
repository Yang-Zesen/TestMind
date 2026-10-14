"""Explicit graph runtime configuration and independent worker bootstrap."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from collections.abc import Mapping
from contextlib import suppress
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
    demo_mode: bool = False

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
            demo_mode=env.get("TESTMIND_DEMO_MODE", "false").lower() == "true",
        )
        if value.demo_mode and (env.get("APP_ENV", "development") != "development" or urlparse(value.uri).hostname not in {"localhost", "127.0.0.1"}):
            raise ValueError("Demo graph runtime requires development and loopback Neo4j")
        if value.enabled and not value.demo_mode and (not all((value.read_user, value.read_password,
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
        ) if not settings.demo_mode else None) as driver, httpx.AsyncClient(timeout=30, follow_redirects=False) as http:
        projection = Neo4jProjection(driver, database=settings.database)
        await projection.ensure_constraints()
        repository = GraphSyncRepository(pool)
        demo_source = None
        if settings.demo_mode:
            from testmind.infrastructure.graph_sync.demo_source import (
                DemoGraphSource,
                seed_demo_graph,
            )
            await seed_demo_graph(pool)
            demo_source = DemoGraphSource(pool)
        def factory(generation: str) -> Neo4jProjection:
            return Neo4jProjection(driver, database=settings.database, generation=generation)
        worker = GraphSyncWorker(
            repository, ActiveGenerationProjection(repository, factory),
            maintenance=GraphMaintenance(repository, factory),
            poller=GraphPoller(repository, demo_source, source_instance="demo-zentao-graph") if demo_source is not None else GraphPoller(repository, SnapshotGraphSource(
                http, url=settings.snapshot_url, token=settings.snapshot_token,
                source_instance=settings.source_instance, project_mapping=settings.project_mapping,
            ), source_instance=settings.source_instance) if settings.snapshot_url else None,
        )
        previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda *_: worker.stop())
        try:
            if demo_source is None:
                await worker.run()
            else:
                while not worker.stopping.is_set():
                    worked = False
                    try:
                        for project in ("demo-project", "other-project"):
                            for resource in ("project", "member", "requirement", "test_case", "bug"):
                                await worker.poller.run(project, resource)
                        worked = await worker.run_once()
                    except Exception:  # noqa: BLE001 - daemon boundary, no raw dependency error text.
                        logging.getLogger(__name__).error("demo_graph_dependency_unavailable")
                    if not worked:
                        with suppress(TimeoutError):
                            await asyncio.wait_for(worker.stopping.wait(), 2)
        finally:
            signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    if os.name == "nt":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(run_worker())
    else:
        asyncio.run(run_worker())
