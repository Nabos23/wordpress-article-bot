from __future__ import annotations

from typing import TypedDict


class Proposal(TypedDict):
    source_url: str
    source_id: int
    source_post_type: str
    source_title: str
    paragraph_old_html: str
    paragraph_new_html: str
    anchor_text: str
    rationale: str
    decision: str  # "pending" | "approved" | "rejected" | "edited" | "manual_required"


class LinkingState(TypedDict, total=False):
    new_article_url: str
    new_article_id: int
    new_article_title: str
    new_article_text: str

    candidates: list[dict]
    proposals: list[Proposal]
    published: list[dict]
