"""Build the main TestMind graph from application-managed resources."""

from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from testmind.infrastructure.jev.jev_client import JevClient
from testmind.infrastructure.milvus.retrieval import Retriever
from testmind.orchestration.context import RequestContext
from testmind.orchestration.nodes.clarify import ClarifyNode
from testmind.orchestration.nodes.coreference_resolution_node import CoreferenceResolutionNode
from testmind.orchestration.nodes.intent_recognition_node import IntentRecognitionNode
from testmind.orchestration.nodes.spell_correction_node import SpellCorrectionNode
from testmind.orchestration.routing import route_by_intent
from testmind.orchestration.state import GraphState
from testmind.workflows.dialogue import create_dialogue_graph


def create_main_graph(
    checkpointer: BaseCheckpointSaver[Any],
    jev_client: JevClient | None,
    *,
    retriever: Retriever | None = None,
    llm: Any | None = None,
    node_overrides: dict[str, Any] | None = None,
    readonly_node: Any | None = None,
    case_design_node: Any | None = None,
) -> CompiledStateGraph[GraphState, RequestContext, GraphState, GraphState]:
    """Compile the input normalization, intent routing and answer workflow."""
    workflow = StateGraph(GraphState, context_schema=RequestContext)

    if node_overrides is None and jev_client is None:
        raise ValueError("JEV client is required unless explicit node adapters are provided")
    nodes = node_overrides if node_overrides is not None else {
        "spell_correction": SpellCorrectionNode(),
        "coreference_resolution": CoreferenceResolutionNode(),
        "intent_recognition": IntentRecognitionNode(cast(JevClient, jev_client)),
        "clarify": ClarifyNode(),
    }
    nodes = {**nodes, "clarify": nodes.get("clarify", ClarifyNode())}
    for node_name, node in nodes.items():
        workflow.add_node(node_name, node)
    workflow.add_node("dialogue", create_dialogue_graph(retriever, llm=llm))
    if case_design_node is not None:
        workflow.add_node("case_design", case_design_node)
        workflow.add_edge("case_design", END)
    if readonly_node is not None:
        workflow.add_node("readonly_tools", readonly_node)
        workflow.add_edge("readonly_tools", END)

    workflow.add_edge(START, "spell_correction")
    workflow.add_edge("spell_correction", "intent_recognition")
    workflow.add_conditional_edges(
        "intent_recognition",
        lambda state: "case_design" if case_design_node is not None and state.get("intent") == "test_case_generation" else "readonly_tools" if readonly_node is not None and state.get("intent") == "readonly_query" else route_by_intent(state),
        {
            "coreference_resolution": "coreference_resolution",
            "clarify": "clarify",
            **({"readonly_tools": "readonly_tools"} if readonly_node is not None else {}),
            **({"case_design": "case_design"} if case_design_node is not None else {}),
        },
    )
    workflow.add_edge("coreference_resolution", "dialogue")
    workflow.add_edge("dialogue", END)
    workflow.add_edge("clarify", END)

    return workflow.compile(checkpointer=checkpointer)
