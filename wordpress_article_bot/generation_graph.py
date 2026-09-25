from __future__ import annotations

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END

from .generation_nodes import (
    node_keyword_intent,
    node_seo_planner,
    node_write_content_granular,
    node_seo_reviewer,
    node_seo_meta,
    node_generate_images,
    node_human_review,
    node_publish,
    node_research,)

from .generation_state import GenerationState


def build_generation_graph(checkpointer: SqliteSaver):
    workflow = StateGraph(GenerationState)

    workflow.add_node("research", node_research)
    workflow.add_node("keyword_intent", node_keyword_intent)
    workflow.add_node("seo_planner", node_seo_planner)
    workflow.add_node("write_content", node_write_content_granular)
    workflow.add_node("seo_reviewer", node_seo_reviewer)
    workflow.add_node("seo_meta", node_seo_meta)
    workflow.add_node("generate_images", node_generate_images)
    workflow.add_node("human_review", node_human_review)
    workflow.add_node("publish", node_publish)

    workflow.set_entry_point("research")
    workflow.add_edge("research", "keyword_intent")
    workflow.add_edge("keyword_intent", "seo_planner")
    workflow.add_edge("seo_planner", "write_content")
    workflow.add_edge("write_content", "seo_reviewer")
    workflow.add_edge("seo_reviewer", "seo_meta")
    workflow.add_edge("seo_meta", "generate_images")
    workflow.add_edge("generate_images", "human_review")
    workflow.add_edge("human_review", "publish")
    workflow.add_edge("publish", END)

    return workflow.compile(checkpointer=checkpointer)
