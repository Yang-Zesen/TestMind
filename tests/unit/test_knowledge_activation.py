import pytest

from testmind.infrastructure.knowledge.activation import KnowledgeActivationService


class FakeRepository:
    def __init__(self):
        self.switched = False
        self.finished = False

    async def get_activation_operation(self, operation_id):
        return {
            "id": operation_id,
            "status": "pending",
            "project_id": "p1",
            "kb_id": "kb1",
            "document_id": "d1",
            "from_document_version": "v0",
            "from_index_version": "i0",
            "to_document_version": "v1",
            "to_index_version": "i1",
            "target_chunk_count": "2",
            "old_chunk_count": "2",
        }

    async def switch_active_version(self, operation_id):
        self.switched = True

    async def finish_activation(self, operation_id):
        self.finished = True


class FakeIndex:
    def __init__(self, repository):
        self.repository = repository
        self.calls = []

    def set_version_active(self, **kwargs):
        self.calls.append((kwargs["document_version"], kwargs["active"]))
        assert not self.repository.switched if kwargs["active"] else self.repository.switched
        return 2


async def test_already_completed_activation_is_a_safe_worker_replay():
    repository = FakeRepository()
    original = repository.get_activation_operation
    async def completed(operation_id):
        return {**await original(operation_id), "status":"succeeded"}
    repository.get_activation_operation = completed
    index = FakeIndex(repository)
    await KnowledgeActivationService(repository, index).activate("already-completed")
    assert index.calls == []


@pytest.mark.asyncio
async def test_activation_sets_target_before_pointer_then_deactivates_old():
    repository = FakeRepository()
    index = FakeIndex(repository)
    service = KnowledgeActivationService(repository, index)

    await service.activate("activation-1")

    assert index.calls == [("v1", True), ("v0", False)]
    assert repository.switched and repository.finished
