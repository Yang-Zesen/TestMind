"""Build the main TestMind graph from application-managed resources."""

from typing import Any

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
    checkpointer: BaseCheckpointSaver,
    jev_client: JevClient,
    *,
    retriever: Retriever | None = None,
    llm: Any | None = None,
) -> CompiledStateGraph:
    """Compile the input normalization, intent routing and answer workflow."""
    workflow = StateGraph(GraphState, context_schema=RequestContext)

    nodes = {
        "spell_correction": SpellCorrectionNode(),
        "coreference_resolution": CoreferenceResolutionNode(),
        "intent_recognition": IntentRecognitionNode(jev_client),
        "clarify": ClarifyNode(),
    }
    for node_name, node in nodes.items():
        workflow.add_node(node_name, node)
    workflow.add_node("dialogue", create_dialogue_graph(retriever, llm=llm))

    workflow.add_edge(START, "spell_correction")
    workflow.add_edge("spell_correction", "intent_recognition")
    workflow.add_conditional_edges(
        "intent_recognition",
        route_by_intent,
        {
            "coreference_resolution": "coreference_resolution",
            "clarify": "clarify",
        },
    )
    workflow.add_edge("coreference_resolution", "dialogue")
    workflow.add_edge("dialogue", END)
    workflow.add_edge("clarify", END)

    return workflow.compile(checkpointer=checkpointer)
