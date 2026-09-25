from __future__ import annotations

import networkx as nx

from . import db


def build_link_graph() -> nx.DiGraph:
    graph = nx.DiGraph()
    pages = db.get_all_pages(with_html_only=False)
    for p in pages:
        graph.add_node(p["url"])
    for source, target in db.get_all_links():
        if graph.has_node(target) and source != target:
            graph.add_edge(source, target)
    return graph


def compute_authority_scores(gsc_metrics: dict[str, dict] | None = None, pagerank_weight: float = 0.4, gsc_weight: float = 0.6) -> dict[str, float]:
    graph = build_link_graph()
    pagerank = nx.pagerank(graph) if graph.number_of_edges() > 0 else {n: 1 / max(len(graph), 1) for n in graph}

    gsc_metrics = gsc_metrics or {}
    max_clicks = max((m["clicks"] for m in gsc_metrics.values()), default=0) or 1
    max_pr = max(pagerank.values()) if pagerank else 1

    scores: dict[str, float] = {}
    for url in graph.nodes:
        pr_score = pagerank.get(url, 0.0) / max_pr if max_pr else 0.0
        gsc_entry = gsc_metrics.get(url) or gsc_metrics.get(url + "/")
        if gsc_entry:
            gsc_score = gsc_entry["clicks"] / max_clicks
            scores[url] = pagerank_weight * pr_score + gsc_weight * gsc_score
        else:
            scores[url] = pr_score
    return scores
