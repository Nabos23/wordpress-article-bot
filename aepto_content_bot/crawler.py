"""
Orchestrates ingestion:
  1. Pull real content (title, HTML) for every post/page via the WP REST API --
     this is the reliable source of actual article/service text.
  2. Pull the full sitemap URL list as a cross-check -- reveals URLs REST didn't
     return (different post types, orphaned entries) so nothing quietly gets missed.
  3. Extract internal link edges from each page's HTML and store them, giving us
     the link graph for authority scoring later.
  4. Persist everything to SQLite (db.py).
"""

from __future__ import annotations

from urllib.parse import urlparse

from . import db
from .config import settings
from .content_utils import clean_text, extract_internal_links, word_count
from .sitemap_client import get_all_sitemap_urls
from .wp_client import WPClient

BASE_DOMAIN = urlparse(settings.wp_base_url).netloc


def run_crawl() -> dict:
    db.init_db()
    wp = WPClient()

    print(f"[crawl] Fetching posts/pages via WP REST API from {settings.wp_base_url} ...")
    records = wp.fetch_site_content()
    print(f"[crawl] Fetched {len(records)} posts/pages from REST.")

    for r in records:
        text = clean_text(r["html"])
        db.upsert_page(
            {
                "url": r["url"],
                "wp_id": r["id"],
                "post_type": r["post_type"],
                "title": r["title"],
                "html": r["html"],
                "clean_text": text,
                "word_count": word_count(text),
                "date_published": r.get("date"),
                "date_modified": r.get("modified"),
            }
        )

    print(f"[crawl] Fetching sitemap from {settings.sitemap_url} ...")
    try:
        sitemap_entries = get_all_sitemap_urls(settings.sitemap_url)
        print(f"[crawl] Sitemap lists {len(sitemap_entries)} URLs.")
        for entry in sitemap_entries:
            db.mark_sitemap_seen(entry["url"], entry["lastmod"])
    except Exception as e:
        print(f"[crawl] Sitemap fetch failed ({e}) -- continuing with REST content only.")
        sitemap_entries = []

    print("[crawl] Building internal link graph ...")
    pages = db.get_all_pages()
    for page in pages:
        targets = extract_internal_links(page["html"], BASE_DOMAIN)
        db.replace_links_for_source(page["url"], targets)

    orphans = db.get_orphaned_sitemap_urls()
    if orphans:
        print(
            f"[crawl] {len(orphans)} URL(s) are in the sitemap but returned no content via REST "
            f"(different post type, or stale sitemap entry) -- see db.get_orphaned_sitemap_urls()."
        )

    return {
        "pages_from_rest": len(records),
        "sitemap_urls": len(sitemap_entries),
        "orphaned_sitemap_urls": len(orphans),
    }
