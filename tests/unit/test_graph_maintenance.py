import pytest

from testmind.infrastructure.graph_sync.maintenance import GraphMaintenance
from testmind.models.graph_sync import GraphEvent


class Repository:
    def __init__(self):
        self.switched = False
        self.event = GraphEvent(event_id="e1", source_instance="testmind", project_id="p1",
                                entity_type="requirement", source_id="1", revision=1,
                                operation="upsert")

    async def snapshot(self, project_id):
        return [self.event]

    async def generation(self, project_id):
        return "initial"

    async def switch_generation(self, project_id, generation, fingerprints):
        self.switched = fingerprints == {self.event.entity_key: self.event.fingerprint}
        return self.switched


class Projection:
    def __init__(self):
        self.events = []

    async def apply(self, event):
        self.events.append(event)
        return True

    async def inventory(self, project_id):
        return {}


async def test_rebuild_switches_only_after_current_snapshot_is_projected():
    repository = Repository()
    projection = Projection()
    result = await GraphMaintenance(repository, lambda generation: projection).rebuild("p1")
    assert repository.switched
    assert result["projected"] == 1
    assert result["generation"] != "initial"


async def test_rebuild_failure_does_not_switch_online_generation():
    class BrokenProjection(Projection):
        async def apply(self, event):
            raise ConnectionError("offline")

    repository = Repository()
    with pytest.raises(ConnectionError):
        await GraphMaintenance(repository, lambda generation: BrokenProjection()).rebuild("p1")
    assert not repository.switched


async def test_reconciliation_replays_authoritative_snapshot_including_tombstones():
    repository = Repository()
    repository.event = repository.event.model_copy(update={"operation": "delete"})
    projection = Projection()
    result = await GraphMaintenance(repository, lambda generation: projection).reconcile("p1")
    assert result["projected"] == 1
    assert projection.events[0].operation == "delete"


async def test_failed_revision_gate_cannot_switch_generation():
    class RefusedProjection(Projection):
        async def apply(self, event):
            return False

    repository = Repository()
    with pytest.raises(RuntimeError):
        await GraphMaintenance(repository, lambda generation: RefusedProjection()).rebuild("p1")
    assert not repository.switched


async def test_rebuild_refreshes_snapshot_when_routine_projection_wins_race():
    repository = Repository()
    class RacingProjection(Projection):
        async def apply(self, event):
            if event.revision == 1:
                repository.event = event.model_copy(update={"revision": 2})
                return False
            return True

        async def current(self, event):
            return {"revision": 2, "fingerprint": repository.event.fingerprint}

    result = await GraphMaintenance(repository, lambda generation: RacingProjection()).rebuild("p1")
    assert repository.switched
    assert result["projected"] == 1
