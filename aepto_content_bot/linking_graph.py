from __future__ import annotations

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END

from .linking_nodes import node_human_review, node_plan_insertions, node_publish, node_retrieve_candidates
from .linking_state import LinkingState


def build_linking_graph(checkpointer: SqliteSaver):
    workflow = StateGraph(LinkingState)

    workflow.add_node("retrieve_candidates", node_retrieve_candidates)
    workflow.add_node("plan_insertions", node_plan_insertions)
    workflow.add_node("human_review", node_human_review)
    workflow.add_node("publish", node_publish)

    workflow.set_entry_point("retrieve_candidates")
    workflow.add_edge("retrieve_candidates", "plan_insertions")
    workflow.add_edge("plan_insertions", "human_review")
    workflow.add_edge("human_review", "publish")
    workflow.add_edge("publish", END)

    return workflow.compile(checkpointer=checkpointer)
