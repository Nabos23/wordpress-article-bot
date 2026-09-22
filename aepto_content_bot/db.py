"""
Persistent store for everything the crawler learns about the site. SQLite instead
of flat JSON files this time -- lets us query/filter as the content set grows, and
survives interleaved runs of ingestion, generation, and linking without needing to
load the whole site into memory each time.

Schema:
    pages(url PK, wp_id, post_type, title, html, clean_text, word_count,
          date_published, date_modified, in_sitemap, sitemap_lastmod, last_crawled)
    links(source_url, target_url, discovered_at)  -- internal link edges, rebuilt each crawl
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url             TEXT PRIMARY KEY,
    wp_id           INTEGER,
    post_type       TEXT,
    title           TEXT,
    html            TEXT,
    clean_text      TEXT,
    word_count      INTEGER,
    date_published  TEXT,
    date_modified   TEXT,
    in_sitemap      INTEGER DEFAULT 0,
    sitemap_lastmod TEXT,
    last_crawled    TEXT
);

CREATE TABLE IF NOT EXISTS links (
    source_url  TEXT,
    target_url  TEXT,
    discovered_at TEXT,
    PRIMARY KEY (source_url, target_url)
);

CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_url);
CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_url);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def upsert_page(page: dict) -> None:
    """page: {url, wp_id, post_type, title, html, clean_text, word_count, date_published, date_modified}"""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO pages (url, wp_id, post_type, title, html, clean_text, word_count,
                                date_published, date_modified, last_crawled)
            VALUES (:url, :wp_id, :post_type, :title, :html, :clean_text, :word_count,
                    :date_published, :date_modified, :last_crawled)
            ON CONFLICT(url) DO UPDATE SET
                wp_id=excluded.wp_id, post_type=excluded.post_type, title=excluded.title,
                html=excluded.html, clean_text=excluded.clean_text, word_count=excluded.word_count,
                date_published=excluded.date_published, date_modified=excluded.date_modified,
                last_crawled=excluded.last_crawled
            """,
            {**page, "last_crawled": dt.datetime.utcnow().isoformat()},
        )


def mark_sitemap_seen(url: str, lastmod: str | None) -> None:
    """
    Flags a URL as present in the sitemap. If it doesn't exist in `pages` yet
    (i.e. REST didn't return it -- could be a non-standard post type, or an
    orphaned/stale sitemap entry), insert a stub row so it still shows up in
    reporting rather than silently disappearing.
    """
    with get_conn() as conn:
        updated = conn.execute(
            "UPDATE pages SET in_sitemap=1, sitemap_lastmod=? WHERE url=?", (lastmod, url.rstrip("/"))
        ).rowcount
        if updated == 0:
            conn.execute(
                """
                INSERT INTO pages (url, in_sitemap, sitemap_lastmod, last_crawled)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(url) DO UPDATE SET in_sitemap=1, sitemap_lastmod=excluded.sitemap_lastmod
                """,
                (url.rstrip("/"), lastmod, dt.datetime.utcnow().isoformat()),
            )


def replace_links_for_source(source_url: str, target_urls: list[str]) -> None:
    """Rebuild the outbound link edges for one page (called once per page per crawl)."""
    now = dt.datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute("DELETE FROM links WHERE source_url=?", (source_url,))
        conn.executemany(
            "INSERT OR IGNORE INTO links (source_url, target_url, discovered_at) VALUES (?, ?, ?)",
            [(source_url, t, now) for t in target_urls],
        )


def get_all_pages(with_html_only: bool = True) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM pages").fetchall()
    pages = [dict(r) for r in rows]
    if with_html_only:
        pages = [p for p in pages if p.get("html")]
    return pages


def get_page(url: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM pages WHERE url=?", (url.rstrip("/"),)).fetchone()
    return dict(row) if row else None


def get_orphaned_sitemap_urls() -> list[dict]:
    """URLs the sitemap claims exist but that REST never returned content for -- worth a manual look."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM pages WHERE in_sitemap=1 AND (html IS NULL OR html='')").fetchall()
    return [dict(r) for r in rows]


def get_all_links() -> list[tuple[str, str]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT source_url, target_url FROM links").fetchall()
    return [(r["source_url"], r["target_url"]) for r in rows]
