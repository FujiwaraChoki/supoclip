"""Pure utility helpers extracted from ``TaskService`` for size hygiene."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict
import hashlib


def build_cache_key(url: str, source_type: str, processing_mode: str) -> str:
    payload = f"{source_type}|{processing_mode}|{url.strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def seconds_to_mmss(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes = total // 60
    secs = total % 60
    return f"{minutes:02d}:{secs:02d}"


def is_queued_task_stale(task: Dict[str, Any], timeout_seconds: int) -> bool:
    """Detect queued tasks that have likely stalled due to worker issues."""
    if task.get("status") != "queued":
        return False

    created_at = task.get("created_at")
    updated_at = task.get("updated_at") or created_at

    if not created_at or not updated_at:
        return False

    now = (
        datetime.now(updated_at.tzinfo)
        if getattr(updated_at, "tzinfo", None)
        else datetime.utcnow()
    )
    age_seconds = (now - updated_at).total_seconds()
    return age_seconds >= timeout_seconds
