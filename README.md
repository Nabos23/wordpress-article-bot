# Aepto Content Bot

An automated WordPress content generation and internal linking system powered by LangGraph, Groq, and Google Gemini.

The bot handles the full content lifecycle in four coordinated parts:

| Part | What it does |
|------|-------------|
| **1 — Ingest** | Crawl the site, cross-check the sitemap, build a link graph + ChromaDB vector index, and cache authority scores (PageRank blended with Google Search Console clicks). |
| **2 — Generate** | Keyword → full article generation with SEO metadata, JSON-LD schema markup, and auto-composited featured/heading images. Human-in-the-loop review before publishing. |
| **3 — Link** | Post-publish internal linking loop — finds the best existing pages to link *to* the new article, drafts the paragraph edits, and applies them after your review. Auto-chains immediately after a live publish; can also be triggered manually for scheduled posts. |
| **4 — API** | FastAPI wrapper over all of the above so you can drive everything over HTTP instead of (or alongside) the CLI. |

---

## Requirements

- Python 3.11+
- A WordPress site with the REST API enabled
- A [Groq](https://console.groq.com/) API key (article generation)
- A [Google Gemini](https://aistudio.google.com/) API key (secondary generation model)
- A [Pexels](https://www.pexels.com/api/) API key (image generation — free tier is fine)
- *(Optional)* A Google Search Console OAuth client (authority scoring; falls back to PageRank-only if missing)

---

## Setup

### 1. Clone & install

```bash
git clone https://github.com/Nabos23/aepto-content-bot.git
cd aepto-content-bot
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in at minimum:

| Variable | Where to get it |
|----------|----------------|
| `WP_BASE_URL` | Your WordPress site URL |
| `WP_APP_USER` | WordPress username |
| `WP_APP_PASSWORD` | WP Admin → Users → Profile → Application Passwords |
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com/) |
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com/) |
| `PEXELS_API_KEY` | [pexels.com/api](https://www.pexels.com/api/) |
| `SEO_PLUGIN` | `yoast` \| `rankmath` \| `none` |

See [`.env.example`](.env.example) for all available options and their defaults.

### 3. WordPress mu-plugin (required for SEO meta to save)

Yoast and RankMath store their meta title/description as *protected* post meta that the WP REST API silently ignores unless explicitly registered. Copy the helper snippet to your WordPress install:

```
wp-content/mu-plugins/register-seo-meta.php
```

*(Create the `mu-plugins` folder if it doesn't exist — WordPress loads everything in it automatically.)*

If you skip this step, `publish` still works but prints a `meta_warning` for the fields that didn't save.

### 4. Google Search Console (optional but recommended)

GSC data is used to blend real click/impression signals into the authority scores that determine which pages are best to link from.

1. Go to **Google Cloud Console → APIs & Services → Credentials → Create Credentials → OAuth client ID** and choose *Desktop app*.
2. Download the JSON and set `GSC_OAUTH_CLIENT_JSON` in your `.env` to point at it.
3. On the first `ingest` run the bot opens a browser to log in with the Google account that has access to your Search Console property. The token is cached to `storage/gsc-token.json` — no repeated prompts.

If GSC is not configured, the bot falls back to PageRank-only scoring and tells you so — nothing fails.

### 5. Image templates

Place your two PNG template files and point `.env` at them:

```
FEATURED_TEMPLATE_PATH=./templates/featured_template.png
HEADING_TEMPLATE_PATH=./templates/heading_template.png
```

Coordinates for the text overlays are defined in `image_gen.py` under `FEATURED_LAYOUT` and `HEADING_LAYOUT` — tweak them to match your actual template dimensions.

---

## CLI Usage

### Part 1 — Ingest

```bash
# Crawl the site, build the link graph + vector index, cache authority scores.
# Run first, and again whenever you want the bot to see new/changed content.
python -m aepto_content_bot.main ingest

# Print a summary of what's in the local store.
python -m aepto_content_bot.main report
```

### Part 2 — Generate an article

```bash
# Draft an article (writes storage/gen_review_<thread-id>.json)
python -m aepto_content_bot.main generate --keyword "ai vulnerability scanner" --status draft

# Schedule for a future date (ISO 8601 local time)
python -m aepto_content_bot.main generate \
    --keyword "wordpress uptime monitoring" \
    --status future \
    --date 2026-10-15T09:00:00

# After reviewing the JSON file, approve and publish
python -m aepto_content_bot.main approve \
    --thread-id gen-xxxx \
    --review-file storage/gen_review_gen-xxxx.json
```

**`--status` options:** `draft` | `publish` (immediate) | `future` (requires `--date`)

The review file contains: `title`, `meta_title`, `meta_description`, `content_html`, `word_count`, `links_used`, `heading_image_target`. Edit any field directly in the JSON before approving. Set `"decision": "rejected"` to discard.

Generated images are saved to `storage/generated_images/` before you're asked to approve — open them to check before committing.

### Part 3 — Internal linking

When you approve with `--status publish` (immediate publish), the linking loop **starts automatically** — watch the console for a `link-xxxx` thread ID and a second review file.

For scheduled posts (or any article you want a fresh linking pass on):

```bash
# Draft link insertions pointing at an existing article
python -m aepto_content_bot.main plan-links \
    --url https://your-site.com/your-article-slug/

# Apply your decisions
python -m aepto_content_bot.main approve-links \
    --thread-id link-xxxx \
    --review-file storage/link_review_link-xxxx.json
```

### Part 4 — API server

```bash
uvicorn aepto_content_bot.app:app --reload --port 8000
```

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `POST` | `/ingest` | Full crawl + re-index |
| `GET` | `/report` | Store summary + top pages by authority |
| `POST` | `/generate` | `{keyword, status, date?}` → draft article, returns `thread_id` + review |
| `GET` | `/generate/{thread_id}/review` | Fetch the current draft for review |
| `POST` | `/generate/{thread_id}/decide` | `{decision, title?, meta_title?, meta_description?, content_html?}` |
| `POST` | `/generate/{thread_id}/publish` | Apply decision, publish/schedule; auto-chains linking if immediately published |
| `POST` | `/links/plan` | `{url}` → draft internal-link insertions pointing at this URL |
| `GET` | `/links/{thread_id}/review` | Fetch proposed link insertions |
| `POST` | `/links/{thread_id}/decide` | `{decisions: [{index, decision, paragraph_new_html?}]}` |
| `POST` | `/links/{thread_id}/publish` | Apply decisions and edit the live pages |

All state is stored in `storage/` (`gen_review_*.json`, `link_review_*.json`, `checkpoints.sqlite`). The API is a thin wrapper over the same functions as the CLI — anything that works from the command line works identically through the API.

---

## Project Structure

```
aepto_content_bots/
├── aepto_content_bot/
│   ├── main.py              # CLI entry point (all commands)
│   ├── app.py               # FastAPI app factory
│   ├── routing.py           # FastAPI route handlers
│   ├── config.py            # Settings loaded from .env
│   │
│   ├── # Part 1 — Ingest
│   ├── crawler.py           # WP REST + sitemap crawl
│   ├── sitemap_client.py    # Sitemap XML parser (handles index + nested)
│   ├── wp_client.py         # WordPress REST API client
│   ├── db.py                # SQLite store (pages, sitemap URLs)
│   ├── indexing.py          # ChromaDB vector index management
│   ├── link_graph.py        # Link graph + PageRank authority scoring
│   ├── content_utils.py     # HTML → clean text, word count
│   │
│   ├── # Part 2 — Generate
│   ├── generation_state.py  # LangGraph state schema
│   ├── generation_nodes.py  # All generation pipeline nodes
│   ├── generation_graph.py  # LangGraph graph assembly
│   ├── seo_meta.py          # Meta title/description + JSON-LD schema
│   ├── image_gen.py         # Pexels + Pillow image compositing
│   │
│   ├── # Part 3 — Link
│   ├── gsc_client.py        # Google Search Console OAuth client
│   ├── ledger.py            # Edit ledger (tracks which pages were touched)
│   ├── linking_state.py     # LangGraph state schema (linking)
│   ├── linking_nodes.py     # Linking pipeline nodes
│   ├── linking_graph.py     # LangGraph graph assembly (linking)
│   │
│   └── # Shared
│       └── __init__.py
│
├── templates/               # Your PNG image templates (not committed)
├── wp-snippets/
│   └── register-seo-meta.php  # mu-plugin: registers protected SEO meta fields
├── storage/                 # Runtime data (gitignored)
├── .env.example             # Template — copy to .env and fill in
├── requirements.txt
└── README.md
```

---

## How the Auto-Chain Works

When you run `approve` with `publish_status == "publish"` (immediate, not scheduled), `cmd_approve` in `main.py`:

1. Re-fetches the newly created post from WordPress.
2. Adds it to the SQLite DB and the ChromaDB vector index immediately — so it's a valid link *target* right away without waiting for a full `ingest`.
3. Automatically starts the Part 3 linking graph for the new article and writes a `link_review_*.json` for you to review.

**Scheduled (`future`) posts don't auto-chain** — there's no watcher for the moment WordPress actually publishes them. Once a scheduled post goes live, run `plan-links --url <its-url>` manually (or wire a cron job that polls recently-modified posts and calls `POST /links/plan`).

---

## Key Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WP_BASE_URL` | — | WordPress site URL |
| `WP_APP_USER` | — | WordPress username |
| `WP_APP_PASSWORD` | — | WordPress Application Password |
| `SITEMAP_URL` | `{WP_BASE_URL}/sitemap.xml` | Sitemap URL (leave blank to use default) |
| `GROQ_API_KEY` | — | Groq API key |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model |
| `GEMINI_API_KEY` | — | Google Gemini API key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model |
| `GSC_SITE_URL` | — | Search Console property URL |
| `GSC_OAUTH_CLIENT_JSON` | — | Path to OAuth client JSON |
| `GSC_LOOKBACK_DAYS` | `90` | Days of GSC data to fetch |
| `ARTICLE_TARGET_WORDS` | `2500` | Soft target word count |
| `ARTICLE_INTERNAL_LINKS` | `4` | Internal links per article |
| `SEO_PLUGIN` | `yoast` | `yoast` \| `rankmath` \| `none` |
| `DEFAULT_AUTHOR_ID` | *(app password owner)* | WP user ID for generated posts |
| `SCHEMA_AUTHOR_NAME` | *(site domain)* | JSON-LD author name |
| `MAX_NEW_LINKS_PER_SOURCE_PAGE` | `2` | Max new links per source page per run |
| `MIN_DAYS_BETWEEN_EDITS_SAME_PAGE` | `30` | Cooldown before re-editing a page |
| `TOP_K_CANDIDATES` | `8` | Vector search top-k for link candidates |
| `MAX_LINK_PROPOSALS_PER_ARTICLE` | `4` | Max link proposals per new article |
| `PEXELS_API_KEY` | — | Pexels API key |
| `FEATURED_TEMPLATE_PATH` | — | Path to featured image PNG template |
| `HEADING_TEMPLATE_PATH` | — | Path to heading image PNG template |
| `STORAGE_DIR` | `./storage` | Runtime data directory |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model |

---

## Known Limitations

- **Word count is a soft target** — check `word_count` in the review file before approving.
- **Schema markup uses today's date** for scheduled posts (schema is embedded at draft time, not at publish time).
- **The mu-plugin step is easy to forget** — silent failure mode unless you check `meta_warning` in the publish response.
- **No automated trigger for scheduled-post linking** — only immediate publishes auto-chain (see above).
