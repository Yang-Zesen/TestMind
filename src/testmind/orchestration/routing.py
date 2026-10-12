"""Routing helpers for the main TestMind graph."""

from typing import Any

_SUPPORTED_INTENTS = {
    "dialogue",
    "test_case_generation",
    "script_generation",
    "graph_query",
    "clarify",
}

# Dialogue needs conversation context; unimplemented routes stop before coreference.
_IMPLEMENTED_ROUTES = {"dialogue": "coreference_resolution", "clarify": "clarify"}


def route_by_intent(state: dict[str, Any]) -> str:
    """Return the next graph node for a normalized intent decision."""
    intent = state.get("intent")
    if intent not in _SUPPORTED_INTENTS:
        return "clarify"
    return _IMPLEMENTED_ROUTES.get(intent, "clarify")
