import pytest

from testmind.models.graph_sync import EntityRef, GraphEvent, decide_revision


def event(revision=1, title="first"):
    return GraphEvent(
        event_id="event-1", source_instance="zentao-dev", project_id="p1",
        entity_type="requirement", source_id="42", revision=revision,
        operation="upsert", properties={"title": title},
    )


def test_entity_identity_includes_source_and_project():
    first = EntityRef("zentao-dev", "p1", "requirement", "42")
    assert first.key == EntityRef("zentao-dev", "p1", "requirement", "42").key
    assert first.key != EntityRef("zentao-dev", "p2", "requirement", "42").key
    assert first.key != EntityRef("zentao-prod", "p1", "requirement", "42").key


def test_revision_decisions_distinguish_replay_conflict_and_stale():
    current = event(3)
    assert decide_revision(current, event(3)) == "duplicate"
    assert decide_revision(current, event(3, "changed")) == "conflict"
    assert decide_revision(current, event(2)) == "superseded"
    assert decide_revision(current, event(4)) == "accepted"
    assert decide_revision(None, current) == "accepted"


def test_fingerprint_is_independent_of_dictionary_order():
    first = event()
    second = first.model_copy(update={"properties": dict(reversed(list(first.properties.items())))})
    assert first.fingerprint == second.fingerprint


def test_rejects_unknown_resource_and_negative_revision():
    with pytest.raises(ValueError):
        event().model_copy().model_validate({**event().model_dump(), "entity_type": "arbitrary"})
    with pytest.raises(ValueError):
        event(-1)
