from types import SimpleNamespace

import pytest

from testmind.infrastructure.postgres import postgresql_client as module


@pytest.mark.asyncio
async def test_client_keeps_one_checkpointer_for_app_lifetime_and_closes_pool(monkeypatch):
    events = []

    class FakePool:
        def __init__(self, conninfo, **kwargs):
            assert conninfo == "postgresql://example/testmind"
            assert kwargs["kwargs"]["autocommit"] is True
            events.append("pool_created")

        async def __aenter__(self):
            events.append("pool_opened")
            return self

        async def __aexit__(self, *args):
            events.append("pool_closed")

        async def getconn(self):
            class Connection:
                async def execute(self, query):
                    events.append("migration_lock_acquired" if "pg_advisory_lock(" in query else "migration_lock_released")
            return Connection()

        async def putconn(self, connection):
            events.append("lock_connection_returned")

    class FakeSaver:
        def __init__(self, pool):
            self.pool = pool

        async def setup(self):
            events.append("tables_ready")

    monkeypatch.setattr(module, "get_config", lambda: SimpleNamespace(postgres_dsn="postgresql://example/testmind"))
    monkeypatch.setattr(module, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(module, "AsyncPostgresSaver", FakeSaver)

    async with module.open_postgresql_client() as client:
        assert client.checkpointer.pool is client.pool
        assert events == ["pool_created", "pool_opened", "migration_lock_acquired", "tables_ready", "migration_lock_released", "lock_connection_returned"]

    assert events == ["pool_created", "pool_opened", "migration_lock_acquired", "tables_ready", "migration_lock_released", "lock_connection_returned", "pool_closed"]


@pytest.mark.asyncio
async def test_missing_dsn_fails_before_opening_pool(monkeypatch):
    monkeypatch.setattr(module, "get_config", lambda: SimpleNamespace(postgres_dsn=""))

    with pytest.raises(RuntimeError, match="POSTGRES_DSN"):
        async with module.open_postgresql_client():
            pass
