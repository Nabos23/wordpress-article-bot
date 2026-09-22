from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .config import settings


def _load() -> list[dict]:
    path = Path(settings.ledger_path)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _save(entries: list[dict]) -> None:
    Path(settings.ledger_path).write_text(json.dumps(entries, indent=2))


def record_insertion(source_url: str, target_url: str, anchor_text: str) -> None:
    entries = _load()
    entries.append(
        {"source_url": source_url, "target_url": target_url, "anchor_text": anchor_text, "date": dt.date.today().isoformat()}
    )
    _save(entries)


def already_linked(source_url: str, target_url: str) -> bool:
    return any(
        e["source_url"].rstrip("/") == source_url.rstrip("/") and e["target_url"].rstrip("/") == target_url.rstrip("/")
        for e in _load()
    )


def new_links_added_to(source_url: str) -> int:
    return sum(1 for e in _load() if e["source_url"].rstrip("/") == source_url.rstrip("/"))


def days_since_last_edit(source_url: str) -> int | None:
    edits = [e for e in _load() if e["source_url"].rstrip("/") == source_url.rstrip("/")]
    if not edits:
        return None
    last = max(dt.date.fromisoformat(e["date"]) for e in edits)
    return (dt.date.today() - last).days


def is_eligible(source_url: str) -> bool:
    if new_links_added_to(source_url) >= settings.max_new_links_per_source_page:
        return False
    days = days_since_last_edit(source_url)
    if days is not None and days < settings.min_days_between_edits_same_page:
        return False
    return True
