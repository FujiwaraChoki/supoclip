"""Poll subscribed channels/feeds and enqueue unseen videos."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .billing_service import BillingService
from .task_service import TaskService


@dataclass
class SourceItem:
    external_id: str
    url: str
    title: str


async def fetch_subscription_items(subscription: dict) -> list[SourceItem]:
    provider = subscription["provider"]
    if provider != "youtube":
        return []
    channel_id = subscription["external_source_id"]
    source_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(source_url, headers={"User-Agent": "SupoClip-AutoImport/1.0"})
        response.raise_for_status()
    root = ET.fromstring(response.content)
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    items = []
    entries = root.findall("atom:entry", namespaces) or root.findall(".//item")
    for entry in entries:
        video_id = entry.findtext("yt:videoId", namespaces=namespaces)
        guid = entry.findtext("guid")
        link_node = entry.find("atom:link", namespaces)
        if link_node is None:
            link_node = entry.find("link")
        link = link_node.get("href") if link_node is not None and link_node.get("href") else entry.findtext("link")
        external_id = video_id or guid or link
        title = entry.findtext("atom:title", namespaces=namespaces) or entry.findtext("title") or "Imported video"
        if external_id and link:
            items.append(SourceItem(str(external_id), str(link), title.strip()))
    return items


async def poll_source_subscriptions(
    db: AsyncSession, redis, user_id: str | None = None
) -> dict:
    result = await db.execute(
        text(
            """
            SELECT * FROM source_subscriptions
            WHERE enabled = true
              AND (:user_id IS NULL OR user_id = :user_id)
              AND (last_checked_at IS NULL OR last_checked_at < NOW() - INTERVAL '5 minutes')
            ORDER BY last_checked_at NULLS FIRST LIMIT 50
            """
        ),
        {"user_id": user_id},
    )
    imported = []
    errors = []
    for row in result.mappings():
        subscription = dict(row)
        try:
            items = await fetch_subscription_items(subscription)
            if not items:
                await db.execute(
                    text("UPDATE source_subscriptions SET last_checked_at = NOW() WHERE id = :id"),
                    {"id": subscription["id"]},
                )
                await db.commit()
                continue
            last_seen = subscription.get("last_seen_item_id")
            new_items = []
            for item in items:
                if item.external_id == last_seen:
                    break
                new_items.append(item)
            settings = json.loads(subscription.get("settings_json") or "{}")
            limit = max(1, min(5, int(settings.get("max_items_per_poll", 1))))
            for item in reversed(new_items[:limit]):
                await BillingService(db).assert_can_create_task(subscription["user_id"])
                task_id = await TaskService(db).create_task_with_source(
                    user_id=subscription["user_id"], url=item.url, title=item.title,
                    processing_mode=settings.get("processing_mode", "fast"),
                    generation_preferences=settings.get("generation_preferences"),
                    workspace_id=subscription.get("workspace_id"),
                )
                await redis.enqueue_job(
                    "process_video_task", task_id, item.url, "youtube",
                    subscription["user_id"], None, None, None, "default",
                    settings.get("processing_mode", "fast"), "vertical", True, {},
                    settings.get("generation_preferences"), _queue_name="supoclip_tasks",
                )
                imported.append({"subscription_id": subscription["id"], "task_id": task_id,
                                 "external_id": item.external_id})
            await db.execute(
                text(
                    """
                    UPDATE source_subscriptions SET last_checked_at = NOW(),
                        last_seen_item_id = :last_seen, updated_at = NOW() WHERE id = :id
                    """
                ),
                {"id": subscription["id"], "last_seen": items[0].external_id},
            )
            await db.commit()
        except Exception as exc:
            await db.rollback()
            errors.append({"subscription_id": subscription["id"], "error": str(exc)})
            await db.execute(
                text("UPDATE source_subscriptions SET last_checked_at = NOW() WHERE id = :id"),
                {"id": subscription["id"]},
            )
            await db.commit()
    return {"imported": imported, "errors": errors}
