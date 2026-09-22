"""
Central configuration for the Aepto content-generation & internal-linking system.
Loaded from environment variables (.env supported).

This is a separate project from the limitlesshost internal-linking-bot -- keep a
separate .env / venv so the two never accidentally cross-write to the wrong site.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    wp_base_url: str = os.environ.get("WP_BASE_URL", "https://aepto.com").rstrip("/")
    wp_app_user: str = os.environ.get("WP_APP_USER", "")
    wp_app_password: str = os.environ.get("WP_APP_PASSWORD", "")

    sitemap_url: str = os.environ.get("SITEMAP_URL", "")  # defaults to {wp_base_url}/sitemap.xml if blank

    groq_api_key: str = os.environ.get("GROQ_API_KEY", "")
    groq_model: str = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
    gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    gsc_site_url: str = os.environ.get("GSC_SITE_URL", "")
    gsc_oauth_client_json: str = os.environ.get("GSC_OAUTH_CLIENT_JSON", "")
    gsc_lookback_days: int = int(os.environ.get("GSC_LOOKBACK_DAYS", "90"))

    storage_dir: Path = field(default_factory=lambda: Path(os.environ.get("STORAGE_DIR", "./storage")))
    embedding_model: str = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

    article_target_words: int = int(os.environ.get("ARTICLE_TARGET_WORDS", "2000"))
    article_internal_links: int = int(os.environ.get("ARTICLE_INTERNAL_LINKS", "4"))

    # "yoast" | "rankmath" | "none" -- determines which post-meta keys we write
    # meta title/description to. Requires the matching mu-plugin snippet (see
    # README) to be installed on the WP site, since these are normally
    # protected fields the REST API silently ignores otherwise.
    seo_plugin: str = os.environ.get("SEO_PLUGIN", "yoast")
    default_author_id: int | None = int(os.environ["DEFAULT_AUTHOR_ID"]) if os.environ.get("DEFAULT_AUTHOR_ID") else None
    schema_author_name: str = os.environ.get("SCHEMA_AUTHOR_NAME", "")  # falls back to the site's domain if blank

    # Post-publish internal linking loop (Part 3)
    max_new_links_per_source_page: int = int(os.environ.get("MAX_NEW_LINKS_PER_SOURCE_PAGE", "2"))
    min_days_between_edits_same_page: int = int(os.environ.get("MIN_DAYS_BETWEEN_EDITS_SAME_PAGE", "30"))
    top_k_candidates: int = int(os.environ.get("TOP_K_CANDIDATES", "8"))
    max_link_proposals_per_article: int = int(os.environ.get("MAX_LINK_PROPOSALS_PER_ARTICLE", "4"))

    # Image generation (featured image + heading image, composited over templates)
    pexels_api_key: str = os.environ.get("PEXELS_API_KEY", "")
    featured_template_path: str = os.environ.get("FEATURED_TEMPLATE_PATH", "")
    heading_template_path: str = os.environ.get("HEADING_TEMPLATE_PATH", "")
    # Section headings matching any of these (case-insensitive substring) are treated
    # as "the conclusion" -- the heading image goes just above whichever section
    # comes immediately before the first match.
    conclusion_heading_keywords: list[str] = field(default_factory=lambda: ["conclusion", "final thoughts", "summary", "wrapping up"])

    def __post_init__(self):
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        (self.storage_dir / "chroma").mkdir(parents=True, exist_ok=True)
        if not self.sitemap_url:
            self.sitemap_url = f"{self.wp_base_url}/sitemap.xml"

    @property
    def db_path(self) -> str:
        return str(self.storage_dir / "site.db")

    @property
    def chroma_dir(self) -> str:
        return str(self.storage_dir / "chroma")

    @property
    def ledger_path(self) -> str:
        return str(self.storage_dir / "ledger.json")

    @property
    def authority_cache_path(self) -> str:
        return str(self.storage_dir / "authority_scores.json")


settings = Settings()
