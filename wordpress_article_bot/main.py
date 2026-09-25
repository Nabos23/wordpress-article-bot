"""
CLI usage
---------

    python -m wordpress_article_bot.main ingest
        Crawls the site, builds the link graph + vector index, and caches
        authority scores (PageRank blended with GSC clicks, if configured).

    python -m wordpress_article_bot.main report

    python -m wordpress_article_bot.main generate --keyword "..." --status future --date ...
    python -m wordpress_article_bot.main approve --thread-id ... --review-file ...
        Part 2. If --status was "publish" (immediate), approve automatically
        chains into the Part 3 linking plan for the new article and prints the
        next command to run.

    python -m wordpress_article_bot.main plan-links --url <new-article-url>
    python -m wordpress_article_bot.main approve-links --thread-id ... --review-file ...
        Part 3, standalone -- use this for an article that was scheduled/drafted
        earlier and has since gone live (or any article you want a fresh linking
        pass on), since the auto-chain above only fires at the moment of an
        immediate publish.
"""

from __future__ import annotations

import argparse
import json
import uuid

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from . import db
from .config import settings
from .content_utils import clean_text, word_count
from .crawler import run_crawl
from .generation_graph import build_generation_graph
from .indexing import add_or_update_document, build_index
from .link_graph import compute_authority_scores
from .linking_graph import build_linking_graph
from .wp_client import WPClient


def cmd_ingest(_args):
    stats = run_crawl()

    print("[ingest] Pulling Search Console performance data...")
    try:
        from . import gsc_client
        gsc_metrics = gsc_client.fetch_page_metrics()
    except Exception as e:
        print(f"[ingest] GSC fetch failed ({e}); falling back to link-graph PageRank only.")
        gsc_metrics = {}

    authority = compute_authority_scores(gsc_metrics)
    with open(settings.authority_cache_path, "w") as f:
        json.dump(authority, f)

    print("[ingest] Building vector index...")
    build_index()
    print(f"[ingest] Done. {stats}")
    return stats


def cmd_report(_args):
    pages = db.get_all_pages(with_html_only=False)
    by_type: dict[str, int] = {}
    for p in pages:
        by_type[p.get("post_type") or "unknown"] = by_type.get(p.get("post_type") or "unknown", 0) + 1

    print(f"Total pages tracked: {len(pages)}")
    for post_type, count in by_type.items():
        print(f"  {post_type}: {count}")

    orphans = db.get_orphaned_sitemap_urls()
    print(f"\nOrphaned sitemap URLs (no REST content found): {len(orphans)}")
    for o in orphans[:10]:
        print(f"  {o['url']}")

    try:
        with open(settings.authority_cache_path) as f:
            scores = json.load(f)
    except FileNotFoundError:
        scores = compute_authority_scores()

    print("\nTop 10 pages by authority score:")
    top_pages = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:10]
    for url, score in top_pages:
        print(f"  {score:.3f}  {url}")

    return {
        "total_pages": len(pages),
        "by_type": by_type,
        "orphaned_sitemap_urls": len(orphans),
        "top_pages_by_authority": [{"url": u, "score": s} for u, s in top_pages],
    }


def cmd_generate(args):
    if args.status == "future" and not args.date:
        raise SystemExit("--date is required when --status future (ISO 8601, e.g. 2026-08-01T09:00:00)")

    thread_id = f"gen-{uuid.uuid4().hex[:8]}"
    initial_state = {"keyword": args.keyword, "publish_status": args.status, "publish_date": args.date}

    with SqliteSaver.from_conn_string(f"{settings.storage_dir}/checkpoints.sqlite") as checkpointer:
        graph = build_generation_graph(checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        result = graph.invoke(initial_state, config=config)

        interrupt_payload = result["__interrupt__"][0].value
        review_path = f"{settings.storage_dir}/gen_review_{thread_id}.json"
        with open(review_path, "w") as f:
            json.dump(interrupt_payload["review"], f, indent=2)

        review = interrupt_payload["review"]
        print(f"Drafted \"{review['title']}\" -- {review['word_count']} words, {len(review['links_used'])} internal links.")
        print(f"Review it here:\n  {review_path}")
        print(f"Then run: python -m wordpress_article_bot.main approve --thread-id {thread_id} --review-file {review_path}")
    return {"thread_id": thread_id, "review_path": review_path, "review": review}


def _start_linking_plan(article_url: str, article_wp_id: int, article_title: str, article_text: str) -> str:
    """Shared by both cmd_plan_links and the auto-chain in cmd_approve."""
    thread_id = f"link-{uuid.uuid4().hex[:8]}"
    initial_state = {
        "new_article_url": article_url,
        "new_article_id": article_wp_id,
        "new_article_title": article_title,
        "new_article_text": article_text,
    }
    with SqliteSaver.from_conn_string(f"{settings.storage_dir}/checkpoints.sqlite") as checkpointer:
        graph = build_linking_graph(checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        result = graph.invoke(initial_state, config=config)

        interrupt_payload = result["__interrupt__"][0].value
        review_path = f"{settings.storage_dir}/link_review_{thread_id}.json"
        with open(review_path, "w") as f:
            json.dump(interrupt_payload["review"], f, indent=2)

        print(f"[linking] {len(interrupt_payload['review'])} candidate internal-link insertions proposed.")
        print(f"[linking] Review them here:\n  {review_path}")
        print(f"[linking] Then run: python -m wordpress_article_bot.main approve-links --thread-id {thread_id} --review-file {review_path}")
    return {"thread_id": thread_id, "review_path": review_path, "review": interrupt_payload["review"]}


def cmd_approve(args):
    with open(args.review_file) as f:
        reviewed = json.load(f)

    decision = {"decision": reviewed.get("decision", "approved")}
    for field in ("title", "meta_title", "meta_description", "content_html"):
        if field in reviewed:
            decision[field] = reviewed[field]

    with SqliteSaver.from_conn_string(f"{settings.storage_dir}/checkpoints.sqlite") as checkpointer:
        graph = build_generation_graph(checkpointer)
        config = {"configurable": {"thread_id": args.thread_id}}
        result = graph.invoke(Command(resume=decision), config=config)

    published = result.get("published_post", {})
    print(json.dumps(published, indent=2))

    link_plan = None
    if published.get("status") == "created" and published.get("wp_status") == "publish":
        # Immediate publish -- make the new article visible to future planning
        # runs right away (no need to wait for a full re-ingest), then kick off
        # the Part 3 linking plan for it automatically.
        print("\n[auto-chain] Article published immediately -- indexing it and starting the linking plan...")
        wp = WPClient()
        created = wp.get_single(published["id"], post_type="posts")
        text = clean_text(created["content"]["rendered"])
        page_record = {
            "url": created["link"].rstrip("/"), "wp_id": created["id"], "post_type": "posts",
            "title": created["title"]["rendered"], "html": created["content"]["rendered"],
            "clean_text": text, "word_count": word_count(text),
            "date_published": created.get("date"), "date_modified": created.get("modified"),
        }
        db.upsert_page(page_record)
        add_or_update_document(page_record)
        link_plan = _start_linking_plan(page_record["url"], page_record["wp_id"], page_record["title"], text)

    return {"published": published, "link_plan": link_plan}


def cmd_plan_links(args):
    wp = WPClient()
    # find the WP id for this URL via a lightweight slug lookup
    page = db.get_page(args.url)
    if page and page.get("wp_id"):
        article = wp.get_single(page["wp_id"], post_type=page.get("post_type", "posts"))
    else:
        raise SystemExit(f"URL not found in local store: {args.url}. Run `ingest` first, or check the URL is correct.")

    text = clean_text(article["content"]["rendered"])
    return _start_linking_plan(article["link"].rstrip("/"), article["id"], article["title"]["rendered"], text)


def cmd_approve_links(args):
    with open(args.review_file) as f:
        reviewed = json.load(f)

    decisions = [
        {"index": item["index"], "decision": item.get("decision", "approved"), "paragraph_new_html": item.get("after")}
        for item in reviewed
    ]

    with SqliteSaver.from_conn_string(f"{settings.storage_dir}/checkpoints.sqlite") as checkpointer:
        graph = build_linking_graph(checkpointer)
        config = {"configurable": {"thread_id": args.thread_id}}
        result = graph.invoke(Command(resume={"decisions": decisions}), config=config)

    for p in result.get("published", []):
        status = p["status"]
        line = f"[{status}] {p['source_url']}  ->  anchor: \"{p['anchor_text']}\""
        if status in ("skipped", "published_but_not_visible", "published_unverified"):
            line += f"  | reason: {p.get('error', 'unknown')}"
        print(line)
    return {"published": result.get("published", [])}


def main():
    parser = argparse.ArgumentParser(description="WordPress content generation & internal linking system")
    sub = parser.add_subparsers(required=True)

    p_ingest = sub.add_parser("ingest", help="Crawl the site and rebuild the knowledge base")
    p_ingest.set_defaults(func=cmd_ingest)

    p_report = sub.add_parser("report", help="Summarize what's currently in the store")
    p_report.set_defaults(func=cmd_report)

    p_generate = sub.add_parser("generate", help="Draft a new article for a keyword")
    p_generate.add_argument("--keyword", type=str, required=True)
    p_generate.add_argument("--status", type=str, choices=["draft", "publish", "future"], default="draft")
    p_generate.add_argument("--date", type=str, default=None)
    p_generate.set_defaults(func=cmd_generate)

    p_approve = sub.add_parser("approve", help="Apply your review decision and publish/schedule the article")
    p_approve.add_argument("--thread-id", type=str, required=True)
    p_approve.add_argument("--review-file", type=str, required=True)
    p_approve.set_defaults(func=cmd_approve)

    p_plan_links = sub.add_parser("plan-links", help="Draft internal-link insertions pointing at an existing article")
    p_plan_links.add_argument("--url", type=str, required=True)
    p_plan_links.set_defaults(func=cmd_plan_links)

    p_approve_links = sub.add_parser("approve-links", help="Apply your linking review decisions and publish them")
    p_approve_links.add_argument("--thread-id", type=str, required=True)
    p_approve_links.add_argument("--review-file", type=str, required=True)
    p_approve_links.set_defaults(func=cmd_approve_links)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()


