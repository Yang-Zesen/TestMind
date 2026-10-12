from contextlib import asynccontextmanager

import pytest

from testmind.infrastructure.postgres.graph_sync_repository import GraphSyncRepository
from testmind.models.graph_sync import GraphEvent


class Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class Connection:
    def __init__(self, existing=None, current=None):
        self.existing = existing
        self.current = current
        self.queries = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql, params=()):
        self.queries.append((sql, params))
        if "SELECT" in sql and "event_receipts" in sql:
            return Cursor(self.existing)
        if "SELECT" in sql and "entities" in sql:
            return Cursor(self.current)
        return Cursor(None)


class Pool:
    def __init__(self, connection):
        self.value = connection

    @asynccontextmanager
    async def connection(self):
        yield self.value


def event(revision=1):
    return GraphEvent(event_id="evt1", source_instance="s1", project_id="p1",
                      entity_type="requirement", source_id="r1", revision=revision,
                      operation="upsert", properties={"title": "Requirement"})


async def test_registration_persists_asset_and_outbox_in_one_transaction():
    connection = Connection()
    result = await GraphSyncRepository(Pool(connection)).register_event(event(), actor_id="svc")
    assert result["status"] == "accepted"
    assert result["job_id"]
    assert any("INSERT INTO graph_sync.entities" in sql for sql, _ in connection.queries)
    assert any("INSERT INTO graph_sync.projection_outbox" in sql for sql, _ in connection.queries)
    assert any("pg_advisory_xact_lock" in sql for sql, _ in connection.queries)


async def test_replayed_receipt_returns_original_job_without_writes():
    incoming = event()
    connection = Connection(existing={"id": "receipt1", "job_id": "job1", "fingerprint": incoming.fingerprint, "status": "accepted"})
    result = await GraphSyncRepository(Pool(connection)).register_event(incoming, actor_id="svc")
    assert result["job_id"] == "job1"
    assert result["replayed"] is True
    assert not any("INSERT" in sql for sql, _ in connection.queries)


async def test_conflicting_event_id_is_not_silently_replayed():
    connection = Connection(existing={"id": "receipt1", "job_id": "job1", "fingerprint": "different", "status": "accepted"})
    result = await GraphSyncRepository(Pool(connection)).register_event(event(), actor_id="svc")
    assert result["status"] == "conflict"
    assert result["job_id"] is None
    assert any("event.conflict" in sql for sql, _ in connection.queries)


async def test_old_revision_records_receipt_without_replacing_entity():
    connection = Connection(current={"payload": event(3).model_dump(mode="json")})
    result = await GraphSyncRepository(Pool(connection)).register_event(event(2), actor_id="svc")
    assert result["status"] == "superseded"
    assert result["job_id"] is None
    assert not any("INSERT INTO graph_sync.entities" in sql for sql, _ in connection.queries)


async def test_scoped_claim_uses_skip_locked_and_project_filter():
    connection = Connection()
    await GraphSyncRepository(Pool(connection)).claim(project_id="p1")
    claim_sql, params = next((sql, params) for sql, params in connection.queries if "SKIP LOCKED" in sql)
    assert "project_id" in claim_sql
    assert "p1" in params


async def test_revoked_grant_is_checked_inside_event_write_transaction():
    connection = Connection()
    with pytest.raises(PermissionError):
        await GraphSyncRepository(Pool(connection)).register_event(event(), actor_id="svc", authorized_user="svc")
    assert not any("INSERT" in sql for sql, _ in connection.queries)
    assert any("project_memberships" in sql and "FOR UPDATE" in sql for sql, _ in connection.queries)


async def test_expired_rebuild_lease_is_checked_against_wall_clock():
    connection = Connection()
    with pytest.raises(PermissionError):
        await GraphSyncRepository(Pool(connection)).switch_generation("p1", "g2", {}, job_id="j1", claim_token="old")
    lease_sql = next(sql for sql, _ in connection.queries if "lease_expires_at" in sql)
    assert "clock_timestamp()" in lease_sql
