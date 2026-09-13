"""Batch processing queue: create, inspect, and control (pause/resume/cancel/
retry) a sequential multi-video processing run."""

import logging
from typing import Any, Dict

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_config
from ...database import get_db
from ...repositories.batch_queue_repository import BatchQueueRepository
from ...services.batch_queue_service import BatchQueueService
from ...workers.job_queue import JobQueue
from .tasks import _get_user_id_from_headers

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/batch-queue", tags=["batch-queue"])


def _redis_client() -> redis.Redis:
    config = get_config()
    return redis.Redis(
        host=config.redis_host, port=config.redis_port, password=config.redis_password, decode_responses=True
    )


async def _require_queue_owner(db: AsyncSession, user_id: str, queue_id: str) -> Dict[str, Any]:
    queue = await BatchQueueRepository.get_queue(db, queue_id)
    if not queue:
        raise HTTPException(status_code=404, detail="Batch queue not found")
    if queue["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized for this batch queue")
    return queue


@router.post("/")
async def create_batch_queue(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.json()
    items = payload.get("items") or []
    if not items or not isinstance(items, list):
        raise HTTPException(status_code=400, detail="At least one item is required")
    for item in items:
        if not item.get("source_filename") or not item.get("source_path"):
            raise HTTPException(
                status_code=400, detail="Each item needs source_filename and source_path"
            )

    user_id = await _get_user_id_from_headers(request, db)
    queue = await BatchQueueService(db).create_batch(
        user_id,
        items,
        template_id=payload.get("template_id"),
        auto_export_to_source=bool(payload.get("auto_export_to_source", False)),
        delay_between_items_seconds=int(payload.get("delay_between_items_seconds", 3)),
    )
    await JobQueue.enqueue_job("process_batch_queue_task", queue["id"])
    return {"batch_queue": queue}


@router.get("/incomplete")
async def list_incomplete_batch_queues(request: Request, db: AsyncSession = Depends(get_db)):
    """For the "Resume batch?" restart-survival prompt."""
    user_id = await _get_user_id_from_headers(request, db)
    queues = await BatchQueueRepository.get_incomplete_queues_for_user(db, user_id)
    return {"batch_queues": queues}


@router.get("/{queue_id}")
async def get_batch_queue(queue_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _get_user_id_from_headers(request, db)
    queue = await _require_queue_owner(db, user_id, queue_id)
    items = await BatchQueueRepository.get_items(db, queue_id)
    return {"batch_queue": queue, "items": items}


@router.post("/{queue_id}/pause")
async def pause_batch_queue(queue_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _get_user_id_from_headers(request, db)
    await _require_queue_owner(db, user_id, queue_id)

    redis_client = _redis_client()
    try:
        await redis_client.setex(f"batch_pause:{queue_id}", 3600, "1")
    finally:
        await redis_client.aclose()
    await BatchQueueService(db).pause_batch(queue_id)
    return {"message": "Batch paused"}


@router.post("/{queue_id}/resume")
async def resume_batch_queue(queue_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    user_id = await _get_user_id_from_headers(request, db)
    await _require_queue_owner(db, user_id, queue_id)

    redis_client = _redis_client()
    try:
        await redis_client.delete(f"batch_pause:{queue_id}", f"batch_cancel:{queue_id}")
    finally:
        await redis_client.aclose()
    await BatchQueueService(db).resume_batch(queue_id, skip_failed=bool(payload.get("skip_failed", False)))
    await JobQueue.enqueue_job("process_batch_queue_task", queue_id)
    return {"message": "Batch resumed"}


@router.post("/{queue_id}/cancel")
async def cancel_batch_queue(queue_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _get_user_id_from_headers(request, db)
    await _require_queue_owner(db, user_id, queue_id)

    redis_client = _redis_client()
    try:
        await redis_client.setex(f"batch_cancel:{queue_id}", 3600, "1")
    finally:
        await redis_client.aclose()
    await BatchQueueService(db).cancel_batch(queue_id)
    return {"message": "Batch cancelled"}


@router.post("/{queue_id}/items/{item_id}/retry")
async def retry_batch_queue_item(
    queue_id: str, item_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _get_user_id_from_headers(request, db)
    await _require_queue_owner(db, user_id, queue_id)

    await BatchQueueService(db).retry_item(item_id)
    # If the batch already finished (or is idle), kick it again so the
    # retried item actually gets picked up rather than sitting as "pending"
    # forever.
    queue = await BatchQueueRepository.get_queue(db, queue_id)
    if queue and queue["status"] in ("completed", "cancelled"):
        await BatchQueueRepository.update_queue_status(db, queue_id, "queued")
        await JobQueue.enqueue_job("process_batch_queue_task", queue_id)
    return {"message": "Item queued for retry"}
