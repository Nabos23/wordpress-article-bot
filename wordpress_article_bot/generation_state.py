from __future__ import annotations

from typing import TypedDict


class LinkTarget(TypedDict):
    url: str
    title: str
    anchor_hint: str  # LLM's suggested anchor phrase, refined during writing


class OutlineSection(TypedDict):
    heading: str
    talking_points: str
    target_words: int
    link_target_url: str | None  # None if this section shouldn't contain a link


class GenerationState(TypedDict, total=False):
    keyword: str
    publish_status: str          # "draft" | "publish" | "future"
    publish_date: str | None     # ISO 8601, required if publish_status == "future"

    # research
    existing_titles: list[str]
    link_candidates: list[LinkTarget]

    # planning
    title: str
    slug: str
    outline: list[OutlineSection]

    # writing
    content_html: str
    word_count: int
    links_used: list[LinkTarget]

    # SEO
    meta_title: str
    meta_description: str
    schema_script: str

    # images
    featured_image_path: str | None
    heading_image_path: str | None
    heading_image_target: str | None  # the outline heading text the image goes above

    # review
    decision: str                # "pending" | "approved" | "edited" | "rejected"

    # result
    published_post: dict
