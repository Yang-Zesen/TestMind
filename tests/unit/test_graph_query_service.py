from types import SimpleNamespace

from testmind.core.security import AuthContext
from testmind.infrastructure.graph_sync.query import GraphQueryService


class Repository:
    async def permission(self, project_id, user_id):
        return {"role": "employee", "permission_version": 1, "can_maintain": False}

    async def generation(self, project_id):
        return "initial"

    async def authorized_entities(self, project_id, owner_id):
        assert project_id == "p1"
        assert owner_id == "u1"
        return {"current": {"revision": 2}, "stale": {"revision": 3}}

    async def audit_query(self, **kwargs):
        pass


class Reader:
    async def read(self, **kwargs):
        assert kwargs["owner_id"] == "u1"
        return [{"entity_key": "current", "revision": 2},
                {"entity_key": "stale", "revision": 1},
                {"entity_key": "deleted", "revision": 5}]


async def test_query_discards_stale_deleted_and_unapproved_entities():
    context = AuthContext("u1", frozenset({"p1"}), frozenset())
    query = SimpleNamespace(project_id="p1", query_type="entities", entity_type=None, limit=50)
    result = await GraphQueryService(Repository(), Reader()).query(query, context)
    assert result["items"] == [{"entity_key": "current", "revision": 2}]
    assert result["stale"] is True


async def test_revoked_pg_membership_denies_graph_query():
    import pytest

    class Revoked(Repository):
        async def permission(self, project_id, user_id):
            return None

    context = AuthContext("u1", frozenset({"p1"}), frozenset())
    query = SimpleNamespace(project_id="p1", query_type="entities", entity_type=None, limit=50)
    with pytest.raises(PermissionError):
        await GraphQueryService(Revoked(), Reader()).query(query, context)


async def test_graph_query_produces_stable_continuation_cursor():
    key = "a" * 64
    class PageRepository(Repository):
        async def authorized_entities(self, project_id, owner_id):
            return {key: {"revision": 2}}

    class PageReader:
        async def read(self, **kwargs):
            assert kwargs["cursor"] is None
            return [{"entity_key": key, "revision": 2}]

    query = SimpleNamespace(project_id="p1", query_type="entities", entity_type=None, limit=1, cursor=None)
    context = AuthContext("u1", frozenset({"p1"}), frozenset())
    result = await GraphQueryService(PageRepository(), PageReader()).query(query, context)
    assert result["next_cursor"] == key
