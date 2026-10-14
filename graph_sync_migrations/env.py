"""Separate migration namespace for graph synchronization."""

import os
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine

if os.getenv("TESTMIND_DEMO_MODE", "false").lower() != "true":
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
config = context.config
url = os.environ.get("GRAPH_SYNC_DSN") or os.environ.get("POSTGRES_DSN")
if not url:
    raise RuntimeError("GRAPH_SYNC_DSN or POSTGRES_DSN is required")
if url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True, version_table="graph_sync_alembic_version")
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url).connect() as connection:
        context.configure(connection=connection, version_table="graph_sync_alembic_version")
        with context.begin_transaction():
            context.run_migrations()
