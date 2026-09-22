"""
WordPress REST API client using an Application Password (Basic Auth).
Create one under WP Admin > Users > Profile > Application Passwords.
"""

from __future__ import annotations

import requests
from requests.auth import HTTPBasicAuth
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings


class WPClient:
    def __init__(self, base_url: str | None = None, user: str | None = None, app_password: str | None = None):
        self.base_url = (base_url or settings.wp_base_url).rstrip("/")
        self.auth = HTTPBasicAuth(user or settings.wp_app_user, app_password or settings.wp_app_password)
        self.session = requests.Session()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        resp = self.session.get(f"{self.base_url}/wp-json/wp/v2/{path}", params=params, auth=self.auth, timeout=30)
        resp.raise_for_status()
        return resp

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _post(self, path: str, json: dict) -> requests.Response:
        resp = self.session.post(f"{self.base_url}/wp-json/wp/v2/{path}", json=json, auth=self.auth, timeout=30)
        resp.raise_for_status()
        return resp

    def get_all(self, post_type: str = "posts", per_page: int = 100, status: str = "publish") -> list[dict]:
        results: list[dict] = []
        page = 1
        while True:
            resp = self._get(post_type, params={"per_page": per_page, "page": page, "status": status})
            batch = resp.json()
            if not batch:
                break
            results.extend(batch)
            total_pages = int(resp.headers.get("X-WP-TotalPages", "1"))
            if page >= total_pages:
                break
            page += 1
        return results

    def get_single(self, post_id: int, post_type: str = "posts") -> dict:
        return self._get(f"{post_type}/{post_id}").json()

    def update_content(self, post_id: int, new_html: str, post_type: str = "posts") -> dict:
        return self._post(f"{post_type}/{post_id}", json={"content": new_html}).json()

    def create_post(
        self,
        title: str,
        content_html: str,
        status: str = "draft",
        date: str | None = None,
        slug: str | None = None,
        categories: list[int] | None = None,
        tags: list[int] | None = None,
        excerpt: str | None = None,
        meta: dict | None = None,
        author: int | None = None,
        featured_media: int | None = None,
        post_type: str = "posts",
    ) -> dict:
        """
        status: "draft" | "publish" | "future" (scheduled -- requires `date` in the future,
                ISO 8601 local time e.g. "2026-08-01T09:00:00")
        meta: passed through to WP's `meta` field -- requires the target meta keys to be
              registered (register_post_meta) or handled by an SEO plugin's REST fields
              (see wp-snippets/register-seo-meta.php).
        featured_media: media attachment ID (from upload_media) to set as the featured image.
        """
        payload: dict = {"title": title, "content": content_html, "status": status}
        if date:
            payload["date"] = date
        if slug:
            payload["slug"] = slug
        if categories:
            payload["categories"] = categories
        if tags:
            payload["tags"] = tags
        if excerpt:
            payload["excerpt"] = excerpt
        if meta:
            payload["meta"] = meta
        if author:
            payload["author"] = author
        if featured_media:
            payload["featured_media"] = featured_media
        return self._post(post_type, json=payload).json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def upload_media(self, file_path: str, filename: str, alt_text: str | None = None) -> dict:
        """
        Uploads a local image to the WP media library. Returns the full media
        object (use ["id"] for featured_media / post meta, ["source_url"] for
        embedding directly in content).
        """
        import mimetypes
        from urllib.parse import quote

        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        headers = {
            "Content-Disposition": f'attachment; filename="{quote(filename)}"',
            "Content-Type": content_type,
        }
        with open(file_path, "rb") as f:
            resp = self.session.post(
                f"{self.base_url}/wp-json/wp/v2/media", headers=headers, data=f, auth=self.auth, timeout=60
            )
        resp.raise_for_status()
        media = resp.json()

        if alt_text:
            try:
                self._post(f"media/{media['id']}", json={"alt_text": alt_text})
            except Exception as e:
                print(f"[wp_client] Could not set alt text on media {media['id']}: {e}")

        return media

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def fetch_public_html(self, url: str) -> str:
        """Plain unauthenticated GET of the live page, same as a visitor would see."""
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text

    def fetch_site_content(self) -> list[dict]:
        """Pull posts + pages, normalize into a flat list of content records."""
        records = []
        for post_type in ("posts", "pages"):
            for item in self.get_all(post_type):
                records.append(
                    {
                        "id": item["id"],
                        "post_type": post_type,
                        "url": item["link"].rstrip("/"),
                        "title": item["title"]["rendered"],
                        "html": item["content"]["rendered"],
                        "modified": item.get("modified"),
                        "date": item.get("date"),
                    }
                )
        return records
