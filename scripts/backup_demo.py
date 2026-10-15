"""Offline PG authority and source-object backup; restores only to a new database."""

import argparse
import hashlib
import json
import os
import subprocess
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import psycopg
from dotenv import load_dotenv
from psycopg import sql

TABLES = ("business.tasks", "business.approvals", "business.case_assets", "business.external_operations",
          "business.demo_grants", "knowledge.project_active_versions", "knowledge.document_versions",
          "graph_sync.sync_watermarks", "graph_sync.entities", "graph_sync.projection_outbox")


def counts(connection):
    return {table: connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(*table.split('.')))).fetchone()[0] for table in TABLES}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("backup", "verify-restore"))
    parser.add_argument("--env-file", default="deploy/demo.env")
    parser.add_argument("--directory", required=True)
    parser.add_argument("--quiesced", action="store_true")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=True)
    if os.getenv("TESTMIND_DEMO_MODE") != "true" or not args.quiesced:
        raise RuntimeError("Offline demo backup requires --quiesced and all application processes stopped")
    directory = Path(args.directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    project = "testmind-acceptance" if Path(args.env_file).stem == "acceptance" else "testmind"
    compose = ["docker", "compose", "-p", project, "--env-file", args.env_file, "-f", "deploy/docker-compose.yaml", "exec", "-T", "postgres"]
    archive = directory / "authority.dump"
    sources = directory / "sources.zip"
    started = time.monotonic()
    if args.action == "backup":
        with psycopg.connect(os.environ["POSTGRES_DSN"]) as connection:
            active = connection.execute("SELECT count(*) FROM business.component_heartbeats WHERE status='ready' AND seen_at>now()-interval '45 seconds'").fetchone()[0]
            if active:
                raise RuntimeError("Stop API and all workers before creating a consistent backup")
            expected = counts(connection)
        with archive.open("wb") as output:
            subprocess.run(compose + ["pg_dump", "-U", "testmind", "-d", "testmind", "-Fc"], stdout=output, check=True)
        root = Path(os.environ["KNOWLEDGE_SOURCE_DIR"])
        with zipfile.ZipFile(sources, "w", zipfile.ZIP_DEFLATED) as output:
            for path in sorted(root.glob("*")):
                if path.is_file() and not path.name.startswith('.'):
                    output.write(path, path.name)
        manifest = {"schema_version": 1, "counts": expected,
                    "sha256": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in (archive.name, sources.name)},
                    "backup_seconds": round(time.monotonic()-started, 3),
                    "consistency": "offline application quiescence", "rpo": "quiesced snapshot",
                    "derivatives": "Rebuild Neo4j from graph_sync entities; reindex Milvus from restored source objects."}
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(json.dumps(manifest))
    else:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        for name, digest in manifest["sha256"].items():
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("Backup digest mismatch")
        database = "testmind_restore_" + uuid4().hex[:12]
        with psycopg.connect(os.environ["POSTGRES_DSN"], autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        with archive.open("rb") as source:
            subprocess.run(compose + ["pg_restore", "-U", "testmind", "-d", database, "--exit-on-error"], stdin=source, check=True)
        restored_dsn = psycopg.conninfo.make_conninfo(os.environ["POSTGRES_DSN"], dbname=database)
        with psycopg.connect(restored_dsn) as connection:
            actual = counts(connection)
            if actual != manifest["counts"]:
                raise RuntimeError("Restored authority counts differ")
            # Compare effective mappings, grants and watermarks, not just table sizes.
            with psycopg.connect(os.environ["POSTGRES_DSN"]) as original:
                for table in ("business.demo_grants", "knowledge.project_active_versions", "graph_sync.sync_watermarks"):
                    query = sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text").format(sql.Identifier(*table.split('.')))
                    if connection.execute(query).fetchall() != original.execute(query).fetchall():
                        raise RuntimeError("Restored permissions, versions or watermarks differ")
        target = directory / "restored-sources"
        target.mkdir(exist_ok=True)
        with zipfile.ZipFile(sources) as archived:
            for name in archived.namelist():
                if Path(name).name != name:
                    raise RuntimeError("Invalid source archive path")
            archived.extractall(target)
        report = {"database": database, "counts_match": True, "grants_versions_watermarks_match": True,
                  "source_objects": len(list(target.iterdir())), "restore_seconds": round(time.monotonic()-started, 3),
                  "projection_and_vector_rebuild": "pending"}
        (directory / "restore-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        subprocess.run(["uv", "run", "python", "scripts/rebuild_restored.py",
                        "--env-file", args.env_file, "--directory", str(directory)], check=True)


if __name__ == "__main__":
    main()
