from testmind.infrastructure.graph_sync.projection import Neo4jProjection
from testmind.models.graph_sync import GraphEvent


class Result:
    async def single(self):
        return {"revision": 2}


class Transaction:
    def __init__(self):
        self.calls = []

    async def run(self, query, **params):
        self.calls.append((query, params))
        return Result()


class Session:
    def __init__(self, transaction):
        self.transaction = transaction

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute_write(self, function, *args):
        return await function(self.transaction, *args)


class Driver:
    def __init__(self):
        self.transaction = Transaction()

    def session(self, **kwargs):
        return Session(self.transaction)


async def test_projection_uses_stable_keys_parameterization_and_revision_gate():
    driver = Driver()
    event = GraphEvent(event_id="e1", source_instance="s1", project_id="p1",
                       entity_type="requirement", source_id="1", revision=2,
                       operation="upsert", properties={"title": "untrusted' text"})
    await Neo4jProjection(driver).apply(event)
    queries = driver.transaction.calls
    assert any("MERGE" in query and "revision" in query for query, _ in queries)
    assert all("untrusted' text" not in query for query, _ in queries)
    assert queries[0][1]["entity_key"] == event.entity_key


async def test_projection_preserves_delete_tombstone_instead_of_removing_revision_guard():
    driver = Driver()
    event = GraphEvent(event_id="e2", source_instance="s1", project_id="p1",
                       entity_type="requirement", source_id="1", revision=3,
                       operation="delete")
    await Neo4jProjection(driver).apply(event)
    assert any(params.get("tombstoned") is True for _, params in driver.transaction.calls)
    assert not any("DETACH DELETE n" in query for query, _ in driver.transaction.calls)
