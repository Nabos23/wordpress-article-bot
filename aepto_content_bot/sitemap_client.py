"""
Pulls the full set of URLs the site itself claims to have, straight from its
sitemap(s). This matters as a cross-check against the WP REST API: REST only
returns published posts/pages of known types, but a sitemap can reveal category
pages, product pages, or other URL types REST wouldn't surface -- and conversely
can reveal broken/orphaned sitemap entries that no longer resolve via REST.

Handles nested sitemap indexes (common with Yoast/RankMath: a root sitemap.xml
that just lists other sitemaps, e.g. post-sitemap.xml, page-sitemap.xml).
"""

from __future__ import annotations

import requests
from lxml import etree
from tenacity import retry, stop_after_attempt, wait_exponential

XML_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def _fetch_xml(url: str) -> etree._Element:
    resp = requests.get(url, timeout=30, headers={"User-Agent": "aepto-content-bot/1.0"})
    resp.raise_for_status()
    return etree.fromstring(resp.content)


def _is_sitemap_index(root: etree._Element) -> bool:
    return root.tag.endswith("sitemapindex")


def get_all_sitemap_urls(root_sitemap_url: str, _depth: int = 0, _max_depth: int = 4) -> list[dict]:
    """
    Returns a flat list of {"url": ..., "lastmod": ...} for every page in the
    sitemap tree, recursing through nested sitemap indexes.
    """
    if _depth > _max_depth:
        return []

    root = _fetch_xml(root_sitemap_url)
    results: list[dict] = []

    if _is_sitemap_index(root):
        for sitemap_el in root.findall("sm:sitemap", XML_NS):
            loc_el = sitemap_el.find("sm:loc", XML_NS)
            if loc_el is None or not loc_el.text:
                continue
            results.extend(get_all_sitemap_urls(loc_el.text.strip(), _depth=_depth + 1, _max_depth=_max_depth))
    else:
        for url_el in root.findall("sm:url", XML_NS):
            loc_el = url_el.find("sm:loc", XML_NS)
            if loc_el is None or not loc_el.text:
                continue
            lastmod_el = url_el.find("sm:lastmod", XML_NS)
            results.append(
                {
                    "url": loc_el.text.strip().rstrip("/"),
                    "lastmod": lastmod_el.text.strip() if lastmod_el is not None and lastmod_el.text else None,
                }
            )

    return results
