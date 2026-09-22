from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .config import settings

router = APIRouter()


def _load_handlers():
    """Deferred import so a missing dependency (e.g. Groq key not set) surfaces as a
    clean 500 with a message, rather than crashing the whole app at import time."""
    try:
        from .main import cmd_approve, cmd_approve_links, cmd_generate, cmd_ingest, cmd_plan_links, cmd_report
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Workflow backend unavailable: {exc}") from exc
    return cmd_ingest, cmd_report, cmd_generate, cmd_approve, cmd_plan_links, cmd_approve_links


def _args(**kwargs) -> Any:
    return type("Args", (), kwargs)()


def _gen_review_path(thread_id: str) -> Path:
    return settings.storage_dir / f"gen_review_{thread_id}.json"


def _link_review_path(thread_id: str) -> Path:
    return settings.storage_dir / f"link_review_{thread_id}.json"


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No review file found at {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Health / ingest
# ---------------------------------------------------------------------------

@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "aepto-content-bot"}


@router.post("/ingest")
def ingest() -> dict[str, Any]:
    try:
        cmd_ingest, *_ = _load_handlers()
        print("[api] /ingest requested")
        stats = cmd_ingest(None)
        return {"status": "ok", "stats": stats}
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[api] /ingest failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/report")
def report() -> dict[str, Any]:
    try:
        _, cmd_report, *_ = _load_handlers()
        return {"status": "ok", "note": "see server console for full report"} | (cmd_report(None) or {})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Part 2: keyword -> article generation
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    keyword: str
    status: str = "draft"  # "draft" | "publish" | "future"
    date: str | None = None  # required if status == "future"


@router.post("/generate")
def generate(request: GenerateRequest) -> dict[str, Any]:
    try:
        if request.status == "future" and not request.date:
            raise HTTPException(status_code=400, detail="'date' is required when status is 'future'")
        _, _, cmd_generate, *_ = _load_handlers()
        print(f"[api] /generate requested for keyword={request.keyword!r}")
        result = cmd_generate(_args(keyword=request.keyword, status=request.status, date=request.date))
        return {"status": "ok", "thread_id": result["thread_id"], "review": result["review"]}
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[api] /generate failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/generate/{thread_id}/review")
def get_generation_review(thread_id: str) -> dict[str, Any]:
    review = _load_json(_gen_review_path(thread_id))
    return {"thread_id": thread_id, "review": review}


class GenerationDecisionPayload(BaseModel):
    decision: str  # "approved" | "edited" | "rejected"
    title: str | None = None
    meta_title: str | None = None
    meta_description: str | None = None
    content_html: str | None = None


@router.post("/generate/{thread_id}/decide")
def decide_generation(thread_id: str, payload: GenerationDecisionPayload) -> dict[str, Any]:
    path = _gen_review_path(thread_id)
    review = _load_json(path)
    review["decision"] = payload.decision
    for field in ("title", "meta_title", "meta_description", "content_html"):
        value = getattr(payload, field)
        if value is not None:
            review[field] = value
    _save_json(path, review)
    return {"status": "ok", "message": "Decision saved", "review_file": str(path)}


@router.post("/generate/{thread_id}/publish")
def publish_generation(thread_id: str) -> dict[str, Any]:
    try:
        *_, cmd_approve, _, _ = _load_handlers()
        review_path = str(_gen_review_path(thread_id))
        print(f"[api] publishing generated article for thread {thread_id}")
        result = cmd_approve(_args(thread_id=thread_id, review_file=review_path))
        return {"status": "ok", **result}
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[api] publish failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Part 3: post-publish internal linking loop
# ---------------------------------------------------------------------------

class PlanLinksRequest(BaseModel):
    url: str


@router.post("/links/plan")
def plan_links(request: PlanLinksRequest) -> dict[str, Any]:
    try:
        *_, cmd_plan_links, _ = _load_handlers()
        print(f"[api] /links/plan requested for {request.url}")
        result = cmd_plan_links(_args(url=request.url))
        return {"status": "ok", "thread_id": result["thread_id"], "review": result["review"]}
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[api] /links/plan failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/links/{thread_id}/review")
def get_links_review(thread_id: str) -> dict[str, Any]:
    review = _load_json(_link_review_path(thread_id))
    return {"thread_id": thread_id, "suggestions": review, "count": len(review)}


class LinkDecision(BaseModel):
    index: int
    decision: str  # "approved" | "rejected" | "edited"
    paragraph_new_html: str | None = None


class LinkDecisionPayload(BaseModel):
    decisions: list[LinkDecision]


@router.post("/links/{thread_id}/decide")
def decide_links(thread_id: str, payload: LinkDecisionPayload) -> dict[str, Any]:
    path = _link_review_path(thread_id)
    review = _load_json(path)
    for d in payload.decisions:
        if 0 <= d.index < len(review):
            review[d.index]["decision"] = d.decision
            if d.decision == "edited" and d.paragraph_new_html:
                review[d.index]["after"] = d.paragraph_new_html
    _save_json(path, review)
    return {"status": "ok", "message": "Decisions saved", "review_file": str(path)}


@router.post("/links/{thread_id}/publish")
def publish_links(thread_id: str) -> dict[str, Any]:
    try:
        *_, cmd_approve_links = _load_handlers()
        review_path = str(_link_review_path(thread_id))
        print(f"[api] publishing approved link decisions for thread {thread_id}")
        result = cmd_approve_links(_args(thread_id=thread_id, review_file=review_path))
        return {"status": "ok", **result}
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[api] links publish failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
