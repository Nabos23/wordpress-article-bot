from __future__ import annotations

import chromadb
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

from . import db
from .config import settings

_embeddings = None


def get_embeddings() -> HuggingFaceEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(model_name=settings.embedding_model)
    return _embeddings


def build_index() -> Chroma:
    """Rebuilds the vector index from whatever is currently in SQLite. Wipes any
    previous collection first so re-running doesn't duplicate every page's
    embedding on top of the last run's."""
    pages = db.get_all_pages()

    client = chromadb.PersistentClient(path=settings.chroma_dir)
    try:
        client.delete_collection("langchain")
    except ValueError:
        pass  # nothing to delete on first run

    docs = [
        Document(
            page_content=f"{p['title']}\n\n{p['clean_text']}",
            metadata={"url": p["url"], "wp_id": p["wp_id"], "post_type": p["post_type"], "title": p["title"]},
        )
        for p in pages
        if p.get("clean_text")
    ]
    ids = [p["url"].rstrip("/") for p in pages if p.get("clean_text")]
    return Chroma.from_documents(docs, embedding=get_embeddings(), persist_directory=settings.chroma_dir, ids=ids)


def load_index() -> Chroma:
    return Chroma(persist_directory=settings.chroma_dir, embedding_function=get_embeddings())


def add_or_update_document(page: dict) -> None:
    """
    Upserts a single page into the existing index without touching anything else.
    Used right after publishing a new article so it's immediately visible as a
    future link target -- without this, you'd have to re-run a full `ingest`
    before the new article could ever be picked as a candidate.
    """
    store = load_index()
    url = page["url"].rstrip("/")
    try:
        store._collection.delete(ids=[url])  # no-op if it doesn't exist yet
    except Exception:
        pass
    doc = Document(
        page_content=f"{page['title']}\n\n{page.get('clean_text', '')}",
        metadata={"url": page["url"], "wp_id": page.get("wp_id"), "post_type": page.get("post_type", "posts"), "title": page["title"]},
    )
    store.add_documents([doc], ids=[url])


def find_topically_related(store: Chroma, text: str, k: int = 8, exclude_url: str | None = None) -> list[dict]:
    results = store.similarity_search_with_relevance_scores(text, k=k + 1)
    out = []
    for doc, score in results:
        if exclude_url and doc.metadata.get("url", "").rstrip("/") == exclude_url.rstrip("/"):
            continue
        out.append(
            {
                "url": doc.metadata["url"],
                "title": doc.metadata["title"],
                "post_type": doc.metadata["post_type"],
                "similarity": score,
            }
        )
    return out[:k]
