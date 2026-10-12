import pytest

from testmind.infrastructure.graph_sync.polling import GraphPoller, PollPage
from testmind.models.graph_sync import GraphEvent


class Source:
    async def fetch_page(self, project_id, resource_type, cursor):
        return PollPage(events=(GraphEvent(event_id="e1", source_instance="s1", project_id=project_id,
                        entity_type="requirement", source_id="1", revision=1,
                        operation="upsert"),), next_cursor="next", has_more=False)


class Repository:
    def __init__(self):
        self.pages = []

    async def watermark(self, **kwargs):
        return None

    async def register_page(self, **kwargs):
        self.pages.append(kwargs)


async def test_cursor_and_events_are_committed_in_one_repository_operation():
    repository = Repository()
    await GraphPoller(repository, Source(), source_instance="s1").run("p1", "requirement")
    assert len(repository.pages) == 1
    assert repository.pages[0]["next_cursor"] == "next"
    assert repository.pages[0]["expected_cursor"] is None


async def test_out_of_scope_page_cannot_advance_watermark():
    class WrongSource(Source):
        async def fetch_page(self, project_id, resource_type, cursor):
            return await super().fetch_page("other", resource_type, cursor)

    repository = Repository()
    with pytest.raises(PermissionError):
        await GraphPoller(repository, WrongSource(), source_instance="s1").run("p1", "requirement")
    assert not repository.pages
