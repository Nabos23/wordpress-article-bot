"""
Meta title/description live in *protected* post meta for both major SEO plugins
(keys prefixed with `_` for Yoast, or simply unregistered for REST for RankMath).
WordPress's REST API silently drops any meta key that hasn't been explicitly
registered with `show_in_rest => true` -- it won't error, the field just never
saves. See mu-plugins/register-seo-meta.php (README) for the snippet that must
be installed on the WP site for this to actually work.
"""

from __future__ import annotations

import json

FIELD_MAP = {
    "yoast": {"title": "_yoast_wpseo_title", "description": "_yoast_wpseo_metadesc"},
    "rankmath": {"title": "rank_math_title", "description": "rank_math_description"},
    "none": {},
}


def build_meta_fields(plugin: str, meta_title: str, meta_description: str) -> dict:
    mapping = FIELD_MAP.get(plugin, {})
    if not mapping:
        return {}
    return {mapping["title"]: meta_title, mapping["description"]: meta_description}


def build_article_schema_script(
    title: str,
    description: str,
    url: str,
    date_published: str,
    author_name: str,
    image_url: str | None = None,
) -> str:
    """Returns a full <script type="application/ld+json"> tag, ready to embed in the post content."""
    schema = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": title,
        "description": description,
        "url": url,
        "datePublished": date_published,
        "author": {"@type": "Organization", "name": author_name},
    }
    if image_url:
        schema["image"] = image_url
    return f'<script type="application/ld+json">{json.dumps(schema)}</script>'


def verify_meta_saved(created_post: dict, expected_meta: dict) -> list[str]:
    """
    Compares what we asked WordPress to save against what actually came back in
    the create_post response. Returns a list of keys that didn't stick -- almost
    always means the mu-plugin registering those meta keys isn't installed.
    """
    actual_meta = created_post.get("meta", {}) or {}
    missing = []
    for key, expected_value in expected_meta.items():
        if actual_meta.get(key) != expected_value:
            missing.append(key)
    return missing
