from testmind.infrastructure.graph_sync.metrics import calculate_metrics
from testmind.models.graph_sync import GraphEvent, RelationSpec


def event(kind, source_id, **kwargs):
    return GraphEvent(event_id=source_id, source_instance="testmind", project_id="p1",
                      entity_type=kind, source_id=source_id, revision=1, operation="upsert", **kwargs)


def test_automation_rate_counts_only_explicit_relationships_to_visible_cases():
    cases = [event("test_case", "c1"), event("test_case", "c2")]
    script = event("script", "s1", relations=(RelationSpec(kind="automates", target_type="test_case",
                   target_source_instance="testmind", target_source_id="c1"),))
    metrics = calculate_metrics([*cases, script])
    assert metrics["automation_rate"] == 0.5
    assert metrics["automated_cases"] == 1
    assert calculate_metrics([script])["automation_rate"] is None


def test_tombstones_and_unresolved_relations_do_not_contribute():
    case = event("test_case", "c1").model_copy(update={"operation": "delete"})
    assert calculate_metrics([case])["test_cases"] == 0
