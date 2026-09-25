from __future__ import annotations

import os
import re
import datetime as dt
from urllib.parse import urlparse

from langchain_groq import ChatGroq
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from typing import TypedDict, Literal, Optional, List, Dict, Any

from . import db
from .config import settings
from langchain_google_genai import ChatGoogleGenerativeAI
from .content_utils import clean_text, word_count
from .generation_state import GenerationState
from .image_gen import find_heading_before_conclusion, generate_featured_image, generate_heading_image, inject_image_above_heading
from .indexing import find_topically_related, load_index
from .link_graph import compute_authority_scores
from .seo_meta import build_article_schema_script, build_meta_fields, verify_meta_saved
from bs4 import BeautifulSoup
from .wp_client import WPClient

_llm = None


def get_llm():
    global _llm
    if _llm is None:
        from langchain_google_genai import ChatGoogleGenerativeAI
        _llm = ChatGoogleGenerativeAI(model=settings.gemini_model, api_key=settings.gemini_api_key, temperature=0.3)
    return _llm


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return re.sub(r"-+", "-", slug)

# ---------------------------------------------------------------------------
# Pydantic Schemas for Structured Output
# ---------------------------------------------------------------------------

class KeywordIntentSchema(BaseModel):
    primary_intent: Literal["informational", "transactional", "commercial", "navigational"] = Field(
        ..., description="Dominant user search intent."
    )
    core_entities: List[str] = Field(
        ..., description="10-15 essential NLP entities/concepts required for deep topical authority."
    )
    lsi_keywords: List[str] = Field(
        ..., description="8-12 secondary and long-tail semantic search terms."
    )
    target_audience: str = Field(..., description="Target audience demographic and expertise level.")


class SubSectionSchema(BaseModel):
    h4: str = Field(..., description="An actionable H4 subheading.")
    talking_points: str = Field(...)
    target_words: int = Field(..., description="Word count target (220-300 words).")
    search_intent: str = Field(...)
    preferred_format: Literal["paragraphs", "bullet_list", "numbered_steps", "comparison_table"] = Field("paragraphs")
    primary_keyword: str = Field(...)
    secondary_keywords: List[str] = Field(default_factory=list)
    entities_to_include: List[str] = Field(default_factory=list)
    
    # Granular link targeting at H4 level
    link_target_url: Optional[str] = Field(
        None, 
        description="If assigned, an existing candidate URL that MUST be hyperlinked inside this sub-block."
    )
    link_target_title: Optional[str] = Field(
        None, 
        description="Title of the page being linked to for descriptive anchor context."
    )

class OutlineSectionSchema(BaseModel):
    h2: str = Field(..., description="Main H2 section heading.")
    intro_summary: str = Field(..., description="A 60-80 word narrative transition introducing this H2.")
    h3_subreadings: List[str] = Field(
        default_factory=list, 
        description="Optional list of 1-2 H3 concept sub-headings to nest inside this section for deeper authority."
    )
    subsections: List[SubSectionSchema] = Field(
        ..., description="2-4 detailed H4 sub-blocks under this H2."
    )
    link_target_url: Optional[str] = Field(None)


class DetailedArticlePlan(BaseModel):
    title: str = Field(..., description="SEO title.")
    meta_title: str = Field(..., description="Meta title under 60 chars.")
    meta_description: str = Field(..., description="Meta description under 155 chars.")
    global_entities: List[str] = Field(..., description="Core entities.")
    outline: List[OutlineSectionSchema] = Field(
        ..., description="MUST contain between 3 to 5 comprehensive main body H2 sections."
    )
    conclusion_summary: str = Field(
        ..., description="Key takeaways and summary points for the mandatory final Conclusion H2 section."
    )

class SubSectionBody(BaseModel):
    html: str = Field(..., description="Clean HTML output containing only elements matching requested format (<p>, <ul>, <ol>, <table>). No H4 tag.")
    summary_for_context: str = Field(..., description="A concise 1-2 sentence summary of what was written here to pass to the next writer pass.")


class SEOEditResult(BaseModel):
    content_html: str = Field(..., description="Refined, polishes article HTML with clean headers, strong transitions, and zero fluff.")
    improvements_made: List[str] = Field(..., description="Short summary of edits made (e.g., removed fluff, added bold lead-ins, fixed heading hierarchy).")


# ---------------------------------------------------------------------------
# LangGraph State Object Definition
# ---------------------------------------------------------------------------

class UpgradedGenerationState(TypedDict):
    keyword: str
    link_candidates: List[Dict[str, str]]
    existing_titles: List[str]
    
    # Intent & Strategy
    search_intent: str
    global_entities: List[str]
    lsi_keywords: List[str]
    
    # Plan
    title: str
    slug: str
    meta_title: str
    meta_description: str
    outline: List[Dict[str, Any]]
    
    # Generation Buffers
    raw_content_html: str
    content_html: str
    word_count: int
    links_used: List[Dict[str, str]]
    
    # Meta / Images / Review
    schema_script: str
    featured_image_path: Optional[str]
    heading_image_path: Optional[str]
    heading_image_target: Optional[str]
    publish_status: str
    publish_date: Optional[str]
    decision: Optional[str]
    published_post: Optional[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def node_research(state: UpgradedGenerationState) -> dict:
    print(f"[workflow] node_research: keyword = {state['keyword']!r}")
    store = load_index()
    
    # Retrieve top 15 related pages to ensure high-quality authority choices
    related = find_topically_related(store, state["keyword"], k=15)

    authority = compute_authority_scores()
    for r in related:
        r["authority"] = authority.get(r["url"].rstrip("/"), 0.0)
    related.sort(key=lambda r: (0.5 * r["authority"] + 0.5 * r["similarity"]), reverse=True)

    # Provide 8-10 solid candidates to the SEO planner
    candidates = [
        {"url": r["url"], "title": r["title"], "anchor_hint": ""}
        for r in related[:10]
    ]

    all_pages = db.get_all_pages(with_html_only=False)
    existing_titles = [p["title"] for p in all_pages if p.get("title")][:40]

    return {"link_candidates": candidates, "existing_titles": existing_titles}


def node_keyword_intent(state: UpgradedGenerationState) -> dict:
    print(f"[workflow] node_keyword_intent: analyzing keyword={state['keyword']!r}")
    llm = get_llm().with_structured_output(KeywordIntentSchema)
    
    prompt = f"""Analyze the search intent and topical ecosystem for the target keyword: "{state['keyword']}".

Identify:
1. Primary search intent (informational, transactional, commercial, navigational).
2. 10-15 essential entity terms, technical concepts, and domain tools that MUST be mentioned for deep topical authority.
3. 8-12 semantic LSI keywords.
4. Target audience persona.
"""
    intent_data: KeywordIntentSchema = llm.invoke(prompt)
    
    return {
        "search_intent": intent_data.primary_intent,
        "global_entities": intent_data.core_entities,
        "lsi_keywords": intent_data.lsi_keywords,
    }


def node_seo_planner(state: UpgradedGenerationState) -> dict:
    print("[workflow] node_seo_planner: drafting long-form H2/H3/H4 blueprint (2200+ words)")
    llm = get_llm().with_structured_output(DetailedArticlePlan)

    candidates_text = "\n".join(f"- {c['url']} ({c['title']})" for c in state["link_candidates"]) or "none"
    existing_titles_text = "\n".join(f"- {t}" for t in state["existing_titles"][:25]) or "none"
    entities_text = ", ".join(state["global_entities"])

    prompt = f"""You are planning an in-depth, authoritative blog article targeting at least 2,200 words.

TARGET KEYWORD: "{state['keyword']}"
SEARCH INTENT: {state['search_intent']}
REQUIRED ENTITIES TO COVER: {entities_text}

EXISTING ARTICLES (Avoid duplicating angles):
{existing_titles_text}

INTERNAL LINKS AVAILABLE:
{candidates_text}

STRICT ARCHITECTURAL REQUIREMENTS:
1. Provide EXACTLY 3 to 6 core main-body H2 sections aside from introduction, conclusion and FAQs.
3. Optionally add 2-4 H3 headings inside an H2 for nested deeper sub-topics/readings.
4. Target word counts MUST be ambitious: each H4 sub-block must target 220-300 words so total word count easily exceeds 2,200 words.
5. Provide detailed takeaways for a mandatory "Conclusion" section.

INTERNAL LINKING REQUIREMENTS
- You MUST assign internal links to AT LEAST 4 to 5 different H4 subsections across the article.
- Use candidate URLs from the list above. Never assign the same URL twice.
- Pick subsections where linking to that existing page fits naturally into the context.
"""
    plan: DetailedArticlePlan = llm.invoke(prompt)

    outline_data = [section.model_dump() for section in plan.outline]
    
    # Append explicit Conclusion section to outline structure
    outline_data.append({
        "h2": "Conclusion",
        "intro_summary": plan.conclusion_summary,
        "h3_subreadings": [],
        "subsections": [
            {
                "h4": "Final Thoughts & Next Steps",
                "talking_points": "Summarize key article insights and provide actionable advice.",
                "target_words": 200,
                "search_intent": "informational",
                "preferred_format": "paragraphs",
                "primary_keyword": state['keyword'],
                "secondary_keywords": [],
                "entities_to_include": []
            }
        ]
    })

    return {
        "title": plan.title,
        "slug": slugify(plan.title),
        "meta_title": plan.meta_title,
        "meta_description": plan.meta_description,
        "global_entities": plan.global_entities,
        "outline": outline_data,
    }


def node_write_content_granular(state: UpgradedGenerationState) -> dict:
    print("[workflow] node_write_content_granular: writing section-by-section at H4 level")
    llm = get_llm().with_structured_output(SubSectionBody)
    candidates_by_url = {c["url"]: c for c in state["link_candidates"]}

    full_article_html: list[str] = []
    links_used = []
    previous_context = "Article start — introduce the topic engagingly."

    for h2_idx, section in enumerate(state["outline"]):
        h2_title = section["h2"]
        full_article_html.append(f"<h2>{h2_title}</h2>")
        
        # Add the brief intro summary for the H2 block
        if section.get("intro_summary"):
            full_article_html.append(f"<p class=\"lead\">{section['intro_summary']}</p>")

        # Handle Internal Link instruction if assigned to this H2
        link_instruction = ""
        if section.get("link_target_url"):
            target = candidates_by_url.get(section["link_target_url"], {})
            target_title = target.get("title", section["link_target_url"])
            link_instruction = (
                f'CRITICAL LINK REQUIREMENT: You MUST include exactly one natural hyperlinked sentence pointing to '
                f'"{target_title}" using anchor URL: {section["link_target_url"]}. Make it feel contextually relevant.'
            )

        for sub_idx, sub in enumerate(section.get("subsections", [])):
            h4_title = sub["h4"]
            fmt = sub["preferred_format"]
            target_words = sub["target_words"]
            entities = ", ".join(sub.get("entities_to_include", [])) or "None specified"
            sec_keywords = ", ".join(sub.get("secondary_keywords", [])) or "None specified"

            # Determine next block heading for smooth context transition
            next_heading = "Next section/Conclusion"
            if sub_idx + 1 < len(section["subsections"]):
                next_heading = section["subsections"][sub_idx + 1]["h4"]
            elif h2_idx + 1 < len(state["outline"]):
                next_heading = state["outline"][h2_idx + 1]["h2"]

            prompt = f"""You are writing ONE specific sub-block of an article.

ARTICLE TITLE: {state['title']}
CURRENT H2: {h2_title}
CURRENT H4 SUBHEADING: {h4_title}
TARGET WORD COUNT: ~{target_words} words
REQUIRED FORMAT: {fmt.upper()}

TALKING POINTS TO COVER:
{sub['talking_points']}

KEYWORDS & ENTITIES TO WEAVE IN:
- Primary Sub-keyword: {sub['primary_keyword']}
- Secondary Keywords: {sec_keywords}
- Technical Entities: {entities}

PREVIOUS SECTION CONTEXT (DO NOT REPEAT CONTENT FROM THIS):
{previous_context}

NEXT UPCOMING TOPIC: {next_heading}

{link_instruction if sub_idx == 0 else ""}

FORMATTING RULES FOR '{fmt}':
- If 'comparison_table': Output clean <table><thead>...</thead><tbody>...</tbody></table>.
- If 'bullet_list': Output <ul> with 3-5 <li> items, each using <strong>Bold Lead-ins</strong>.
- If 'numbered_steps': Output an <ol> with actionable step-by-step <li> items.
- If 'paragraphs': Output 2-3 focused <p> paragraphs with varied sentence length.

DO NOT output the <h4> tag itself. Output ONLY the inner HTML content ({fmt}).
"""
            try:
                sub_body: SubSectionBody = llm.invoke(prompt)
                
                full_article_html.append(f"<h4>{h4_title}</h4>")
                full_article_html.append(sub_body.html)
                
                # Update rolling context
                previous_context = sub_body.summary_for_context

                # Track links
                if section.get("link_target_url") and f'href="{section["link_target_url"]}"' in sub_body.html:
                    if section["link_target_url"] not in [l["url"] for l in links_used]:
                        target = candidates_by_url.get(section["link_target_url"], {})
                        links_used.append({
                            "url": section["link_target_url"],
                            "title": target.get("title", ""),
                            "anchor_hint": h4_title
                        })

            except Exception as exc:
                print(f"[workflow] Error writing subsection '{h4_title}': {exc}")
                continue

    combined_html = "\n\n".join(full_article_html)
    total_words = word_count(clean_text(combined_html))

    print(f"[workflow] node_write_content_granular: completed draft. Word count = {total_words}")
    return {
        "raw_content_html": combined_html,
        "word_count": total_words,
        "links_used": links_used
    }


def node_seo_reviewer(state: UpgradedGenerationState) -> dict:
    print("[workflow] node_seo_reviewer: reviewing content & injecting Custom HTML block")
    
    # Run LLM review/polish pass
    llm = get_llm().with_structured_output(SEOEditResult)
    prompt = f"""You are an elite SEO Editor reviewing a complete draft.

ARTICLE TITLE: {state['title']}
PRIMARY KEYWORD: {state['keyword']}
TARGET ENTITIES: {', '.join(state['global_entities'])}

DRAFT HTML TO REVIEW:
{state['raw_content_html']}

YOUR TASKS:
1. Eliminate repetitive introductory fluff or repetitive boilerplate transitions.
2. Ensure heading tags follow strict hierarchy (h2 -> h4) and that formatting tags (<ul>, <ol>, <table>) are properly formatted.
3. Ensure bullet lists use <strong>bold lead-ins</strong> for readability.
4. Keep all existing internal anchor links intact — DO NOT remove <a href="..."> links!
5. Return the cleaned, polished HTML.
"""
    
    try:
        reviewed: SEOEditResult = llm.invoke(prompt)
        final_html = reviewed.content_html
    except Exception:
        final_html = state["raw_content_html"]

    # Inject your Gutenberg Custom HTML block above the 3rd H2
    final_html = inject_custom_html_above_3rd_h2(final_html, custom_html=CUSTOM_HTML_SNIPPET)

    total_words = word_count(clean_text(final_html))
    print(f"[workflow] node_seo_reviewer: complete. Final word count = {total_words}")
    
    return {
        "content_html": final_html,
        "word_count": total_words
    }


def node_seo_meta(state: GenerationState) -> dict:
    print("[workflow] node_seo_meta: building schema markup")
    # published_post not known yet at draft/review time -- use a placeholder URL,
    # corrected after publish if needed (schema is embedded before publish, so we
    # construct the expected URL from the slug).
    expected_url = f"{settings.wp_base_url}/{state['slug']}/"
    author_name = settings.schema_author_name or urlparse(settings.wp_base_url).netloc
    schema_script = build_article_schema_script(
        title=state["title"],
        description=state["meta_description"],
        url=expected_url,
        date_published=state.get("publish_date") or dt.date.today().isoformat(),
        author_name=author_name,
    )
    return {"schema_script": schema_script}


def node_generate_images(state: GenerationState) -> dict:
    print("[workflow] node_generate_images: generating featured + heading images")

    featured_path = None
    try:
        featured_path = generate_featured_image(state["title"])
    except Exception as exc:
        print(f"[workflow] node_generate_images: featured image failed: {exc}")

    heading_path = None
    heading_target = find_heading_before_conclusion(state["outline"])
    if heading_target:
        try:
            heading_path = generate_heading_image(heading_target)
        except Exception as exc:
            print(f"[workflow] node_generate_images: heading image failed: {exc}")
    else:
        print("[workflow] node_generate_images: no suitable heading found to place the heading image above")

    return {
        "featured_image_path": featured_path,
        "heading_image_path": heading_path,
        "heading_image_target": heading_target,
    }


def node_human_review(state: GenerationState) -> dict:
    """
    Pauses the graph and hands the full draft to a human for one review pass.
    Resume with Command(resume={"decision": "approved"|"rejected"|"edited",
    "title": ..., "meta_title": ..., "meta_description": ..., "content_html": ...})
    -- only include overridden fields when decision == "edited".
    """
    print("[workflow] node_human_review: waiting for human review")
    review_payload = {
        "keyword": state["keyword"],
        "title": state["title"],
        "slug": state["slug"],
        "meta_title": state["meta_title"],
        "meta_description": state["meta_description"],
        "word_count": state["word_count"],
        "links_used": state["links_used"],
        "content_html": state["content_html"],
        "schema_script": state["schema_script"],
        "publish_status": state["publish_status"],
        "publish_date": state.get("publish_date"),
        "featured_image_path": state.get("featured_image_path"),
        "heading_image_path": state.get("heading_image_path"),
        "heading_image_target": state.get("heading_image_target"),
    }

    decision = interrupt({"review": review_payload})
    print(f"[workflow] node_human_review: decision = {decision.get('decision')}")

    updates: dict = {"decision": decision.get("decision", "rejected")}
    for field in ("title", "meta_title", "meta_description", "content_html"):
        if decision.get(field):
            updates[field] = decision[field]
    return updates


def node_publish(state: GenerationState) -> dict:
    if state["decision"] not in ("approved", "edited"):
        print(f"[workflow] node_publish: skipped (decision = {state['decision']})")
        return {"published_post": {"status": "not_published", "decision": state["decision"]}}

    print(f"[workflow] node_publish: creating post with status={state['publish_status']!r}")
    wp = WPClient()

    content_html = state["content_html"]

    featured_media_id = None
    if state.get("featured_image_path"):
        try:
            media = wp.upload_media(state["featured_image_path"], os.path.basename(state["featured_image_path"]), alt_text=state["title"])
            featured_media_id = media["id"]
            print(f"[workflow] node_publish: featured image uploaded (media id {featured_media_id})")
        except Exception as exc:
            print(f"[workflow] node_publish: featured image upload failed: {exc}")

    if state.get("heading_image_path") and state.get("heading_image_target"):
        try:
            media = wp.upload_media(
                state["heading_image_path"], os.path.basename(state["heading_image_path"]),
                alt_text=state["heading_image_target"],
            )
            content_html = inject_image_above_heading(
                content_html, state["heading_image_target"], media["source_url"], state["heading_image_target"]
            )
            print(f"[workflow] node_publish: heading image uploaded and injected above {state['heading_image_target']!r}")
        except Exception as exc:
            print(f"[workflow] node_publish: heading image upload/injection failed: {exc}")

    full_content = f"{content_html}\n\n{state['schema_script']}"
    meta_fields = build_meta_fields(settings.seo_plugin, state["meta_title"], state["meta_description"])

    created = wp.create_post(
        title=state["title"],
        content_html=full_content,
        status=state["publish_status"],
        date=state.get("publish_date"),
        slug=state["slug"],
        meta=meta_fields,
        author=settings.default_author_id,
        featured_media=featured_media_id,
    )

    result = {"status": "created", "id": created["id"], "url": created["link"], "wp_status": created["status"]}

    if meta_fields:
        missing = verify_meta_saved(created, meta_fields)
        if missing:
            result["meta_warning"] = (
                f"These meta fields did not save: {missing}. Install wp-snippets/register-seo-meta.php "
                f"as a mu-plugin on the site, then update the post's meta manually or re-run publish."
            )
            print(f"[workflow] node_publish: WARNING - {result['meta_warning']}")

    if state["publish_status"] == "publish":
        try:
            live_html = wp.fetch_public_html(created["link"])
            missing_links = [
                l["url"] for l in state["links_used"]
                if f'href="{l["url"]}"' not in live_html and l["url"] not in live_html
            ]
            result["links_verified"] = len(state["links_used"]) - len(missing_links)
            result["links_missing_on_live_page"] = missing_links
        except Exception as e:
            result["link_verification_error"] = str(e)

    return {"published_post": result}


## Helper Functions:

def render_latest_posts_block(posts_to_show: int = 4, display_date: bool = True, display_image: bool = True) -> str:
    """Generates valid Gutenberg comment markup for the Latest Posts block."""
    import json
    
    attrs = {
        "postsToShow": posts_to_show,
        "displayPostDate": display_date,
        "displayFeaturedImage": display_image,
        "featuredImageAlign": "left",
        "featuredImageSizeSlug": "thumbnail"
    }
    
    # Standard dynamic Gutenberg block comment format
    return f"\n\n<!-- wp:latest-posts {json.dumps(attrs)} /-->\n\n"


def render_callout_box(text: str, title: str = "Key Takeaway") -> str:
    """Generates a Gutenberg Group / Callout block."""
    return f"""
<!-- wp:group {{"style":{{"color":{{"background":"#f0f4f8"}},"spacing":{{"padding":{{"top":"20px","right":"20px","bottom":"20px","left":"20px"}}}}}},"backgroundColor":"light-gray"}} -->
<div class="wp-block-group has-background" style="background-color:#f0f4f8;padding:20px;">
    <!-- wp:heading {{"level":4}} -->
    <h4 class="wp-block-heading">💡 {title}</h4>
    <!-- /wp:heading -->
    <!-- wp:paragraph -->
    <p>{text}</p>
    <!-- /wp:paragraph -->
</div>
<!-- /wp:group -->
"""


CUSTOM_HTML_SNIPPET = """
<style>
    .in-feed-cta {
        display: flex;
        align-items: center;
        justify-content: space-between;
        background: linear-gradient(90deg, #1e293b 0%, #0f172a 100%);
        padding: 24px 32px;
        border-radius: 12px;
        margin: 35px 0; /* Spacing between paragraphs */
        border-left: 5px solid #6366f1; /* Brand accent stripe */
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
        gap: 20px;
    }

    .cta-content {
        flex: 1;
    }

    .cta-content h4 {
        margin: 0 0 8px 0;
        color: #ffffff;
        font-family: 'Inter', sans-serif;
        font-size: 1.25rem;
        font-weight: 700;
    }

    .cta-content p {
        margin: 0;
        color: #94a3b8;
        font-size: 0.95rem;
        line-height: 1.5;
    }

    .cta-button {
        background-color: #6366f1;
        color: #ffffff !important;
        padding: 12px 24px;
        border-radius: 8px;
        font-weight: 600;
        font-size: 0.9rem;
        text-decoration: none;
        white-space: nowrap;
        transition: all 0.2s ease;
        border: 1px solid rgba(255, 255, 255, 0.1);
    }

    .cta-button:hover {
        background-color: #4f46e5;
        box-shadow: 0 0 15px rgba(99, 102, 241, 0.4);
        transform: translateY(-1px);
    }

    /* Responsive adjustment for mobile */
    @media (max-width: 650px) {
        .in-feed-cta {
            flex-direction: column;
            text-align: center;
            padding: 24px;
        }
        .cta-button {
            width: 100%;
        }
    }
</style>

<div class="in-feed-cta">
    <div class="cta-content">
        <h4>Ready to scale your Domain Monitoring?</h4>
        <p>Explore how our AI Domain Monitoring System can save you hours of manual work every week.</p>
    </div>
    <a href="#" class="cta-button">Try it for free!</a>
</div>
"""


def wrap_in_gutenberg_html_block(html_code: str) -> str:
    """Wraps raw HTML into a native Gutenberg Custom HTML block."""
    return f"\n\n<!-- wp:html -->\n{html_code.strip()}\n<!-- /wp:html -->\n\n"


def inject_custom_html_above_3rd_h2(content_html: str, custom_html: str = CUSTOM_HTML_SNIPPET) -> str:
    """
    Finds the 3rd <h2> heading in the content and inserts 
    the Gutenberg Custom HTML block directly above it.
    """
    soup = BeautifulSoup(content_html, "html.parser")
    h2_tags = soup.find_all("h2")

    # Verify there are at least 3 H2 headings
    if len(h2_tags) < 3:
        print("[wp_blocks] Warning: Less than 3 H2 headings found. Appending custom HTML before last H2.")
        target_h2 = h2_tags[-1] if h2_tags else None
    else:
        target_h2 = h2_tags[2]  # 0-indexed -> 3rd H2 element

    if target_h2:
        block_markup = wrap_in_gutenberg_html_block(custom_html)
        block_soup = BeautifulSoup(block_markup, "html.parser")
        target_h2.insert_before(block_soup)

    return str(soup)