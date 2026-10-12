from testmind.worker.graph_sync_worker import GraphSyncWorker


class Repository:
    async def claim(self, **kwargs):
        return {"id": "m1", "claim_token": "token", "operation": "rebuild",
                "project_id": "p1", "payload": {"generation": "g2"}}

    async def finish(self, **kwargs):
        self.finished = kwargs
        return True

    async def renew(self, **kwargs):
        return True

    async def fail(self, **kwargs):
        raise AssertionError(kwargs)


class Maintenance:
    async def rebuild(self, project_id, *, generation, lease):
        assert (project_id, generation) == ("p1", "g2")
        assert lease == {"job_id": "m1", "claim_token": "token"}


async def test_rebuild_is_dispatched_by_leased_worker():
    repository = Repository()
    await GraphSyncWorker(repository, None, maintenance=Maintenance()).run_once()
    assert repository.finished == {"job_id": "m1", "claim_token": "token"}
