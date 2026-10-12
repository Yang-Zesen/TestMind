"""Metrics over authorized authoritative snapshots, never inferred relationships."""

from collections import Counter
from typing import Any

from testmind.models.graph_sync import EntityRef, GraphEvent


def calculate_metrics(events: list[GraphEvent]) -> dict[str, Any]:
    visible = {event.entity_key: event for event in events if event.operation != "delete"}
    counts = Counter(event.entity_type for event in visible.values())
    cases = {key for key, event in visible.items() if event.entity_type == "test_case"}
    automated = set()
    for event in visible.values():
        if event.entity_type != "script":
            continue
        for relation in event.relations:
            if relation.kind == "automates" and relation.target_type == "test_case":
                target = EntityRef(relation.target_source_instance, event.project_id,
                                   relation.target_type, relation.target_source_id).key
                if target in cases:
                    automated.add(target)
    return {"requirements": counts["requirement"], "test_cases": len(cases),
            "scripts": counts["script"], "test_runs": counts["test_run"], "bugs": counts["bug"],
            "automated_cases": len(automated),
            "automation_rate": len(automated)/len(cases) if cases else None,
            "basis": "current authorized non-tombstoned cases; explicit automates relations"}
