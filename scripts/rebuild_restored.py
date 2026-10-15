"""Rebuild derived stores from an offline restore without changing live resources."""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from dotenv import load_dotenv
from init_demo import ensure_collection
from neo4j import AsyncGraphDatabase
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pymilvus import MilvusClient

from testmind.core.config import get_config
from testmind.infrastructure.graph_sync.maintenance import GraphMaintenance
from testmind.infrastructure.graph_sync.projection import Neo4jProjection
from testmind.infrastructure.graph_sync.runtime import GraphSettings
from testmind.infrastructure.knowledge.chunking import split_document
from testmind.infrastructure.knowledge.document_parser import parse_document_bounded
from testmind.infrastructure.knowledge.ingestion import (
    KnowledgeIngestionHandler,
    _sparse_at,
    _vector_at,
)
from testmind.infrastructure.knowledge.milvus_index import MilvusKnowledgeIndex
from testmind.infrastructure.knowledge.source_storage import FilesystemSourceStorage
from testmind.infrastructure.postgres.graph_sync_repository import GraphSyncRepository
from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository


async def rebuild(directory: Path) -> None:
    report = json.loads((directory / "restore-report.json").read_text(encoding="utf-8"))
    database = report["database"]
    if not database.startswith("testmind_restore_") or not database.replace("_", "").isalnum():
        raise ValueError("Restoration must target a generated recovery database")
    config = get_config()
    if not config.demo_mode:
        raise ValueError("Recovery drill requires demo mode")
    started = time.monotonic()
    dsn = psycopg.conninfo.make_conninfo(os.environ["POSTGRES_DSN"], dbname=database)
    collection = "testmind_restore_" + uuid4().hex[:12]
    client = MilvusClient(config.milvus_uri, timeout=30)
    try:
        ensure_collection(client, collection)
        index = MilvusKnowledgeIndex(client, collection_name=collection, timeout_seconds=30)
        storage = FilesystemSourceStorage(directory / "restored-sources")
        import torch
        from pymilvus.model.hybrid import BGEM3EmbeddingFunction

        torch.set_num_threads(4)
        embedder = BGEM3EmbeddingFunction(model_name=config.embedding_model, device=config.embedding_device, use_fp16=config.embedding_fp16)
        documents = chunks_total = 0
        projections = {}
        async with AsyncConnectionPool(dsn, open=False, kwargs={"row_factory": dict_row, "autocommit": True}) as pool:
            await pool.open()
            repository = KnowledgeRepository(pool)
            handler = KnowledgeIngestionHandler(repository, storage, embedder, index, model_version=Path(config.embedding_model).name)
            async with pool.connection() as connection:
                cursor = await connection.execute("""SELECT v.id FROM knowledge.project_active_versions a
                    JOIN knowledge.document_versions v ON v.document_id=a.document_id
                    AND v.document_version=a.document_version AND v.index_version=a.index_version
                    JOIN knowledge.documents d ON d.id=v.document_id WHERE d.tombstoned_at IS NULL""")
                versions = await cursor.fetchall()
            for version in versions:
                inputs = await repository.get_version_processing_inputs(version["id"])
                if inputs is None:
                    raise RuntimeError("Active version missing processing inputs")
                suffix = Path(inputs["storage_key"]).suffix.lower()
                media = {".md": "text/markdown", ".txt": "text/plain", ".pdf": "application/pdf", ".markdown": "text/markdown"}[suffix]
                with storage.open(inputs["storage_key"]) as source:
                    parsed = await parse_document_bounded(source.read(25 * 1024 * 1024 + 1), media, filename="source" + suffix)
                chunks = split_document(parsed, version_id=inputs["version_id"])
                if not chunks:
                    raise RuntimeError("Active version rebuilt zero chunks")
                vectors = await asyncio.to_thread(embedder.encode_documents, [chunk.content for chunk in chunks])
                records = [handler._record(inputs, chunk, _vector_at(vectors["dense"], i), _sparse_at(vectors["sparse"], i)) for i, chunk in enumerate(chunks)]
                if index.upsert_inactive(records) != len(chunks):
                    raise RuntimeError("Restored vector write verification failed")
                activated = index.set_version_active(project_id=inputs["project_id"], kb_id=inputs["kb_id"], document_id=inputs["document_id"], document_version=inputs["document_version"], index_version=inputs["index_version"], active=True)
                if activated != len(chunks):
                    raise RuntimeError("Restored vector activation verification failed")
                documents += 1
                chunks_total += len(chunks)
            graphs = GraphSyncRepository(pool)
            settings = GraphSettings.from_env()
            async with AsyncGraphDatabase.driver(settings.uri, auth=None) as driver:
                def factory(generation):
                    return Neo4jProjection(driver, database=settings.database, generation=generation)
                await factory("recovery").ensure_constraints()
                async with pool.connection() as connection:
                    cursor = await connection.execute("SELECT DISTINCT project_id FROM graph_sync.entities ORDER BY project_id")
                    projects = await cursor.fetchall()
                for project in projects:
                    result = await GraphMaintenance(graphs, factory).rebuild(project["project_id"])
                    snapshot = await graphs.snapshot(project["project_id"])
                    for event in snapshot:
                        current = await factory(result["generation"]).current(event)
                        if current is None or current["revision"] != event.revision or current["fingerprint"] != event.fingerprint:
                            raise RuntimeError("Restored graph entity does not match authority")
                    projections[project["project_id"]] = result
        report.update({"projection_and_vector_rebuild": "verified", "restored_collection": collection,
            "active_documents": documents, "active_chunks": chunks_total, "graph_projects": projections,
            "rebuild_seconds": round(time.monotonic() - started, 3)})
        (directory / "restore-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--env-file", default="deploy/demo.env")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=True)
    os.environ["PYTHON_DOTENV_DISABLED"] = "true"
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(rebuild(Path(args.directory).resolve()))


if __name__ == "__main__":
    main()
