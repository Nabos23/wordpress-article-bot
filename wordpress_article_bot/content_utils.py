from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag


def clean_text(html: str) -> str:
    """Strip HTML down to readable text for embeddings / LLM context."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def word_count(text: str) -> int:
    return len(text.split())


def extract_internal_links(html: str, base_domain: str) -> list[str]:
    """Return absolute URLs of same-domain links found in this HTML."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(f"https://{base_domain}", a["href"])
        if urlparse(href).netloc.endswith(base_domain):
            links.append(href.split("#")[0].rstrip("/"))
    return links


def get_paragraphs(html: str) -> list[dict]:
    """Split content into indexable, individually-editable paragraph blocks (<p> only)."""
    soup = BeautifulSoup(html, "html.parser")
    paragraphs = []
    for i, p in enumerate(soup.find_all("p")):
        text = p.get_text(strip=True)
        if len(text) < 40:
            continue
        paragraphs.append({"index": i, "text": text, "html": str(p)})
    return paragraphs


def _normalize(text: str) -> str:
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", text).strip()


def replace_paragraph_fuzzy(html: str, old_paragraph_html: str, new_paragraph_html: str, min_similarity: float = 0.9) -> str:
    """
    Finds the <p> in `html` whose normalized text most closely matches the
    originally-planned paragraph, then swaps in the new paragraph as a parsed
    element. Tolerant of quote-character drift / whitespace changes since planning.
    """
    from difflib import SequenceMatcher

    soup = BeautifulSoup(html, "html.parser")
    old_text = _normalize(BeautifulSoup(old_paragraph_html, "html.parser").get_text())

    best_match: Tag | None = None
    best_score = 0.0
    for p in soup.find_all("p"):
        score = SequenceMatcher(None, old_text, _normalize(p.get_text())).ratio()
        if score > best_score:
            best_score = score
            best_match = p

    if best_match is None or best_score < min_similarity:
        raise ValueError(
            f"Could not confidently locate the original paragraph (best match "
            f"similarity: {best_score:.2f}, need >= {min_similarity}). Re-run ingest/plan."
        )

    new_tag = BeautifulSoup(new_paragraph_html, "html.parser").find("p")
    best_match.replace_with(new_tag)
    return str(soup)


def existing_outbound_link_count(html: str, base_domain: str) -> int:
    return len(extract_internal_links(html, base_domain))


def likely_page_builder_managed(html: str, min_paragraph_chars: int = 300) -> bool:
    """
    Heuristic: very little real paragraph text in content.rendered usually means
    the page's actual visible content lives in a page builder's own data
    (Elementor's _elementor_data), not the standard REST content field.
    """
    paragraphs = get_paragraphs(html)
    total_chars = sum(len(p["text"]) for p in paragraphs)
    return total_chars < min_paragraph_chars


def anchor_present_in_rendered_html(html: str, href: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    target = href.rstrip("/")
    for a in soup.find_all("a", href=True):
        if a["href"].rstrip("/") == target or a["href"].rstrip("/").endswith(target.split("//", 1)[-1]):
            return True
    return False
