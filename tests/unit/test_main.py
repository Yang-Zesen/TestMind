import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from testmind import main


def test_windows_database_startup_uses_selector_event_loop():
    loop = main.selector_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_lifespan_injects_client_into_compiled_graph_and_closes_jev(monkeypatch):
    lifecycle = []

    class FakeJev:
        async def aclose(self):
            lifecycle.append("jev_closed")

    fake_jev = FakeJev()

    @asynccontextmanager
    async def fake_postgres():
        lifecycle.append("postgres_open")
        yield SimpleNamespace(checkpointer=InMemorySaver(), pool=object())
        lifecycle.append("postgres_closed")

    monkeypatch.setattr(main.JevClient, "from_config", lambda: fake_jev)
    monkeypatch.setattr(main, "open_postgresql_client", fake_postgres)

    async with main.lifespan(main.app):
        assert main.app.state.jev_client is fake_jev
        edges = {(edge.source, edge.target) for edge in main.app.state.graph.get_graph().edges}
        assert ("spell_correction", "intent_recognition") in edges
        assert ("intent_recognition", "coreference_resolution") in edges
        assert ("coreference_resolution", "dialogue") in edges
        assert ("intent_recognition", "clarify") in edges

    assert lifecycle == ["postgres_open", "postgres_closed", "jev_closed"]
