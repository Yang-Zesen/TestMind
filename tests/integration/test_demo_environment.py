"""Real-service checks for the isolated local demonstration environment."""

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


@pytest.mark.integration
def test_existing_migrations_upgrade_isolated_demo_database(monkeypatch):
    dsn = os.getenv("DEMO_TEST_DSN")
    if not dsn:
        pytest.skip("DEMO_TEST_DSN is required")
    monkeypatch.setenv("POSTGRES_DSN", dsn.replace("postgresql://", "postgresql+psycopg://"))
    monkeypatch.setenv("GRAPH_SYNC_DSN", dsn)
    for name in ("knowledge_alembic.ini", "graph_sync_alembic.ini", "task_alembic.ini"):
        command.upgrade(Config(name), "head")
        command.upgrade(Config(name), "head")
    with create_engine(os.environ["POSTGRES_DSN"]).connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM knowledge.knowledge_bases")).scalar() >= 0
        assert connection.execute(text("SELECT count(*) FROM graph_sync.project_memberships")).scalar() >= 0
        assert connection.execute(text("SELECT count(*) FROM business.tasks")).scalar() >= 0
