from __future__ import annotations

import json
from urllib.parse import urlparse

from langchain_groq import ChatGroq
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from . import db, ledger
from .config import settings
from .content_utils import (
    anchor_present_in_rendered_html,
    existing_outbound_link_count,
    get_paragraphs,
    likely_page_builder_managed,
    replace_paragraph_fuzzy,
)
from .indexing import find_topically_related, load_index
from .linking_state import LinkingState
from .wp_client import WPClient

BASE_DOMAIN = urlparse(settings.wp_base_url).netloc

_llm = None


def get_llm():
    global _llm
    if _llm is None:
        _llm = ChatGroq(model=settings.groq_model, api_key=settings.groq_api_key, temperature=0.3)
    return _llm


class ParagraphChoice(BaseModel):
    should_link: bool = Field(..., description="Whether this page can naturally, honestly link to the new article")
    paragraph_index: int = Field(..., description="Index of the chosen paragraph, -1 if should_link is false")
    anchor_text: str = Field(..., description="Natural anchor text (3-6 words, not generic like 'click here')")
    new_paragraph_html: str = Field(..., description="Full replacement <p>...</p> HTML, identical to the original "
                                                       "except for one added or lightly reworded sentence containing "
                                                       "the link to the new article")
    rationale: str = Field(..., description="One sentence on why this paragraph/anchor makes sense")


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def node_retrieve_candidates(state: LinkingState) -> dict:
    print("[linking] node_retrieve_candidates: starting")
    try:
        with open(settings.authority_cache_path) as f:
            authority = json.load(f)
    except FileNotFoundError:
        authority = {}

    store = load_index()
    related = find_topically_related(
        store, state["new_article_text"], k=settings.top_k_candidates, exclude_url=state["new_article_url"]
    )

    # dedupe by URL, keep best similarity
    seen = {}
    for r in related:
        url = r["url"].rstrip("/")
        if url not in seen or r["similarity"] > seen[url]["similarity"]:
            seen[url] = r
    related = list(seen.values())

    candidates = []
    for r in related:
        url = r["url"].rstrip("/")
        page = db.get_page(url)
        if not page or not page.get("html"):
            continue
        if ledger.already_linked(url, state["new_article_url"]):
            continue
        if not ledger.is_eligible(url):
            continue
        if likely_page_builder_managed(page["html"]):
            candidates.append(
                {**r, "id": page["wp_id"], "post_type": page["post_type"], "html": page["html"],
                 "authority": authority.get(url, 0.0), "needs_manual_edit": True}
            )
            continue
        candidates.append(
            {**r, "id": page["wp_id"], "post_type": page["post_type"], "html": page["html"], "authority": authority.get(url, 0.0)}
        )

    # blend authority + similarity, penalize pages already saturated with bot links
    for c in candidates:
        if c.get("needs_manual_edit"):
            c["combined_score"] = c["authority"]
            continue
        prior_links = ledger.new_links_added_to(c["url"].rstrip("/"))
        c["combined_score"] = (0.5 * c["authority"] + 0.5 * c["similarity"]) - (prior_links * 0.25)

    candidates.sort(key=lambda c: c["combined_score"], reverse=True)
    selected = candidates[: settings.max_link_proposals_per_article]
    print(f"[linking] node_retrieve_candidates: {len(selected)} candidates selected")
    return {"candidates": selected}


def node_plan_insertions(state: LinkingState) -> dict:
    print("[linking] node_plan_insertions: starting")
    llm = get_llm().with_structured_output(ParagraphChoice)
    proposals = []
    used_anchors: list[str] = []

    for cand in state["candidates"]:
        if cand.get("needs_manual_edit"):
            proposals.append(
                {
                    "source_url": cand["url"], "source_id": cand["id"], "source_post_type": cand["post_type"],
                    "source_title": cand["title"], "paragraph_old_html": "", "paragraph_new_html": "",
                    "anchor_text": "",
                    "rationale": "Page appears to be built with a page builder (e.g. Elementor); its real content "
                                 "isn't in content.rendered, so this needs a manual link added in the page editor.",
                    "decision": "manual_required",
                }
            )
            continue

        paragraphs = get_paragraphs(cand["html"])
        if not paragraphs:
            continue
        existing_links = existing_outbound_link_count(cand["html"], BASE_DOMAIN)

        prompt = f"""You are editing an existing web page to add ONE natural internal link to a
newly published article, without disrupting the page's flow or overselling the link.

EXISTING PAGE: "{cand['title']}" ({cand['post_type']})
This page already has {existing_links} outbound internal links -- do not make it feel stuffed.

NEW ARTICLE TO LINK TO:
Title: {state['new_article_title']}
URL: {state['new_article_url']}
Summary: {state['new_article_text'][:800]}

ANCHOR TEXT ALREADY USED ELSEWHERE FOR THIS SAME ARTICLE (pick a genuinely different phrase):
{json.dumps(used_anchors) if used_anchors else "none yet"}

CANDIDATE PARAGRAPHS (choose the single best fit, or decline if none fit honestly):
{json.dumps(paragraphs, indent=2)}

Rules:
- Only propose a link if it is topically relevant and reads naturally, not forced SEO.
- Prefer adding one short clause/sentence over rewriting the whole paragraph.
- Anchor text must describe the new article, not be generic, and differ from anchors listed above.
- Return paragraph_new_html as a complete <p>...</p> element with an <a href="{state['new_article_url']}">...</a> inside it.
- If nothing fits well, set should_link to false.
"""
        try:
            result: ParagraphChoice = llm.invoke(prompt)
        except Exception as exc:
            print(f"[linking] node_plan_insertions: LLM failed for {cand['title']}: {exc}")
            continue

        if not result.should_link or result.paragraph_index < 0:
            continue
        try:
            old_paragraph_html = paragraphs[result.paragraph_index]["html"]
        except IndexError:
            continue

        used_anchors.append(result.anchor_text)
        proposals.append(
            {
                "source_url": cand["url"], "source_id": cand["id"], "source_post_type": cand["post_type"],
                "source_title": cand["title"], "paragraph_old_html": old_paragraph_html,
                "paragraph_new_html": result.new_paragraph_html, "anchor_text": result.anchor_text,
                "rationale": result.rationale, "decision": "pending",
            }
        )

    print(f"[linking] node_plan_insertions: {len(proposals)} proposals created")
    return {"proposals": proposals}


def node_human_review(state: LinkingState) -> dict:
    print("[linking] node_human_review: waiting for human review")
    review_payload = [
        {
            "index": i, "source_url": p["source_url"], "source_title": p["source_title"],
            "anchor_text": p["anchor_text"], "rationale": p["rationale"],
            "before": p["paragraph_old_html"], "after": p["paragraph_new_html"], "decision": "approved",
        }
        for i, p in enumerate(state["proposals"])
    ]

    decisions = interrupt({"review": review_payload})

    updated = list(state["proposals"])
    for d in decisions.get("decisions", []):
        idx = d["index"]
        updated[idx]["decision"] = d["decision"]
        if d["decision"] == "edited" and d.get("paragraph_new_html"):
            updated[idx]["paragraph_new_html"] = d["paragraph_new_html"]

    return {"proposals": updated}


def node_publish(state: LinkingState) -> dict:
    print("[linking] node_publish: starting")
    wp = WPClient()
    published = []

    for p in state["proposals"]:
        if p["decision"] not in ("approved", "edited"):
            continue

        current = wp.get_single(p["source_id"], post_type=p["source_post_type"])
        current_html = current["content"]["rendered"]

        try:
            new_html = replace_paragraph_fuzzy(current_html, p["paragraph_old_html"], p["paragraph_new_html"])
        except ValueError as e:
            published.append({**p, "status": "skipped", "error": str(e)})
            continue

        wp.update_content(p["source_id"], new_html, post_type=p["source_post_type"])

        try:
            live_html = wp.fetch_public_html(p["source_url"])
            verified = anchor_present_in_rendered_html(live_html, state["new_article_url"])
        except Exception as e:
            published.append({**p, "status": "published_unverified", "error": str(e)})
            continue

        if verified:
            ledger.record_insertion(p["source_url"], state["new_article_url"], p["anchor_text"])
            published.append({**p, "status": "published_verified"})
        else:
            published.append({
                **p, "status": "published_but_not_visible",
                "error": "Link not found on the live public page after publishing -- likely a page-builder "
                         "layout overriding content.rendered. Needs a manual fix in the page editor.",
            })

    return {"published": published}
