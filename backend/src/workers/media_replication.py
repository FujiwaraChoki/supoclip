"""Retry media replicas and publish storage health independently of web traffic."""
import asyncio
import logging

from ..media_storage import MediaStorage

logger = logging.getLogger(__name__)


async def check_media_storage(ctx):
    storage = MediaStorage()
    if not getattr(storage.config, "media_fallback_enabled", False):
        return
    report = await asyncio.to_thread(storage.client.health_check, storage.prefix)
    if report["status"] != "healthy":
        logger.error("Media storage health degraded: providers=%s replication=%s",
                     report["providers"], report["replication"])
    return report


async def replicate_pending_media(ctx):
    storage = MediaStorage()
    if not getattr(storage.config, "media_fallback_enabled", False):
        return
    return await asyncio.to_thread(storage.client.sync_pending, limit=20)
