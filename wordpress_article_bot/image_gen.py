"""
Single-article image generation for the WordPress content pipeline.

Generates two images per article:
  1. Featured image  -- built from the article's full title + FEATURED_TEMPLATE_PATH
  2. Heading image    -- built from one chosen H2 heading + HEADING_TEMPLATE_PATH
                        (by default, the section right before "Conclusion")

Each is a Pollinations photo composited into the matching template at fixed pixel
coordinates, with the text drawn on top.
"""

from __future__ import annotations

import io
import os
import random
import re
from dataclasses import dataclass
import urllib.parse
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

from .config import settings


@dataclass
class ImageLayout:
    """Pixel coordinates for photo placement, title box, gradient, and font --
    one instance per template, since the featured and heading templates may be
    different canvas sizes."""

    photo_l: int = 284
    photo_t: int = 388
    photo_r: int = 986
    photo_b: int = 755
    grad_x1: int = 0
    grad_y1: int = 0
    grad_x2: int = 0
    grad_y2: int = 0
    title_l: int = 1132
    title_t: int = 388
    title_r: int = 1825
    title_b: int = 776
    font_size: int = 58


FEATURED_LAYOUT = ImageLayout()
HEADING_LAYOUT = ImageLayout(
    photo_l=910, photo_t=240, photo_r=1822, photo_b=810,
    title_l=82, title_t=309, title_r=780, title_b=790,
    font_size=66,
)


# ---------------------------------------------------------------------------
# Rendering primitives
# ---------------------------------------------------------------------------

def _fetch_pexels_image(query: str) -> Image.Image | None:
    """
    Generate an image using Pollinations.ai.

    Returns:
        PIL.Image in RGBA mode, or None if generation fails.
    """
    try:
        prompt = (
            f"High-quality professional blog illustration of {query}, "
            "clean, modern, realistic, detailed, 16:9, no text, "
            "no watermark, suitable for a business article"
        )

        url = (
            "https://image.pollinations.ai/prompt/"
            + urllib.parse.quote(prompt)
        )

        resp = requests.get(
            url,
            params={
                "width": 1280,
                "height": 720,
                "seed": random.randint(0, 99999999),
                "nologo": "true",
            },
            timeout=60,
        )
        resp.raise_for_status()

        return Image.open(io.BytesIO(resp.content)).convert("RGBA")

    except Exception as exc:
        print(f"  [image_gen] Pollinations error for '{query}': {exc}")
        return None


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    for path in [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "C:/Windows/Fonts/verdanab.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    words, lines, cur = text.split(), [], ""
    for word in words:
        test = f"{cur} {word}".strip()
        if tmp.textbbox((0, 0), test, font=font)[2] <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _paste_photo_exact(base: Image.Image, photo: Image.Image, left: int, top: int, right: int, bottom: int) -> None:
    aw, ah = right - left, bottom - top
    pw, ph = photo.size
    if pw / ph > aw / ah:
        nw = int(ph * aw / ah)
        photo = photo.resize((nw, ah), Image.LANCZOS)
        off = (nw - aw) // 2
        photo = photo.crop((off, 0, off + aw, ah))
    else:
        nh = int(pw * ah / aw)
        photo = photo.resize((aw, nh), Image.LANCZOS)
        off = (nh - ah) // 2
        photo = photo.crop((0, off, aw, off + ah))
    base.paste(photo.convert("RGB"), (left, top))


def _draw_gradient(base: Image.Image, x1: int, y1: int, x2: int, y2: int) -> None:
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width = x2 - x1
    for x in range(width):
        t = x / max(width - 1, 1)
        r = int(30 * (1 - t))
        g = int(20 * (1 - t))
        b = int(180 * (1 - t))
        draw.line([(x1 + x, y1), (x1 + x, y2)], fill=(r, g, b, 255))
    base.paste(Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB"), (0, 0))


def _draw_title(base: Image.Image, title: str, left: int, top: int, right: int, bottom: int, font_size: int) -> None:
    aw, ah = right - left, bottom - top
    font = _get_font(font_size)
    lines = _wrap_text(title, font, aw - 60)
    line_h = font_size + 10
    total = len(lines) * line_h
    canvas = ImageDraw.Draw(base)
    y = top + (ah - total) // 2

    for line in lines:
        line_w = canvas.textbbox((0, 0), line, font=font)[2]
        x_right = right - line_w - 40
        canvas.text((x_right + 2, y + 3), line, font=font, fill=(0, 0, 0, 160))
        canvas.text((x_right, y), line, font=font, fill=(255, 255, 255))
        y += line_h


def _render_banner(template_path: str, text: str, layout: ImageLayout) -> Image.Image:
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template not found: {template_path}")
    banner = Image.open(template_path).convert("RGB")

    photo = _fetch_pexels_image(text)
    if photo:
        _paste_photo_exact(banner, photo, layout.photo_l, layout.photo_t, layout.photo_r, layout.photo_b)

    _draw_gradient(banner, layout.grad_x1, layout.grad_y1, layout.grad_x2, layout.grad_y2)
    _draw_title(banner, text, layout.title_l, layout.title_t, layout.title_r, layout.title_b, layout.font_size)
    return banner


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", text.strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    return f"{cleaned[:120]}.png"


def _output_dir() -> str:
    path = settings.storage_dir / "generated_images"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


# ---------------------------------------------------------------------------
# Public, single-article API
# ---------------------------------------------------------------------------

def generate_featured_image(title: str) -> str:
    """Renders the featured image from the article's full title. Returns the local file path."""
    if not settings.featured_template_path:
        raise ValueError("FEATURED_TEMPLATE_PATH is not set in .env")
    banner = _render_banner(settings.featured_template_path, title, FEATURED_LAYOUT)
    out_path = os.path.join(_output_dir(), f"featured_{_safe_filename(title)}")
    banner.save(out_path)
    print(f"[image_gen] Featured image saved: {out_path}")
    return out_path


def generate_heading_image(heading: str) -> str:
    """Renders the heading image from one chosen H2 heading. Returns the local file path."""
    if not settings.heading_template_path:
        raise ValueError("HEADING_TEMPLATE_PATH is not set in .env")
    banner = _render_banner(settings.heading_template_path, heading, HEADING_LAYOUT)
    out_path = os.path.join(_output_dir(), f"heading_{_safe_filename(heading)}")
    banner.save(out_path)
    print(f"[image_gen] Heading image saved: {out_path}")
    return out_path


def find_heading_before_conclusion(outline: list[dict]) -> str | None:
    """
    Given the article's outline, returns the heading text of the section 
    immediately before the first one that looks like a conclusion.

    Supports both 'h2' (new schema) and 'heading' (old schema).
    """
    if len(outline) < 2:
        return None

    for i, section in enumerate(outline):
        # Handle both key naming strategies
        heading_text = section.get("h2") or section.get("heading", "")
        heading_lower = heading_text.lower()

        if any(kw in heading_lower for kw in settings.conclusion_heading_keywords):
            if i == 0:
                return None  # conclusion is the very first section -- nothing to place it above
            prev_section = outline[i - 1]
            return prev_section.get("h2") or prev_section.get("heading")

    # No explicit conclusion found -> default to section right before the last one
    target_section = outline[-2]
    return target_section.get("h2") or target_section.get("heading")


def inject_image_above_heading(html: str, heading_text: str, image_url: str, alt_text: str) -> str:
    """
    Inserts an image block immediately above the matching <h2> tag.
    Generates standard Gutenberg block markup (<!-- wp:image -->) for Gutenberg compatibility.
    """
    soup = BeautifulSoup(html, "html.parser")
    target = None
    for h2 in soup.find_all("h2"):
        if h2.get_text(strip=True).lower() == heading_text.strip().lower():
            target = h2
            break

    if target is None:
        raise ValueError(f"Could not find an <h2> matching {heading_text!r} to inject the image above.")

    # Gutenberg Block wrapped markup
    gutenberg_img_html = f"""
<!-- wp:image {{"sizeSlug":"full","linkDestination":"none"}} -->
<figure class="wp-block-image size-full">
    <img src="{image_url}" alt="{alt_text}" class="wp-image-injected"/>
</figure>
<!-- /wp:image -->
"""
    img_soup = BeautifulSoup(gutenberg_img_html, "html.parser")
    target.insert_before(img_soup)
    return str(soup)