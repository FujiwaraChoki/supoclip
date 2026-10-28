"""The AI edit job runs on its own queue through a real Redis and arq worker."""

import json

import pytest
from arq import create_pool
from arq.worker import Worker

from src.editor_document import EditDocument
from src.services import ai_edit_service as ai
from src.workers.ai_worker import AIWorkerSettings
from src.workers.job_queue import JobQueue


@pytest.mark.asyncio
async def test_queued_instruction_is_processed_by_the_ai_worker(db_session, monkeypatch):
    async def fake_plan(context):
        assert "make it black and white" in context
        return ai.EditPlan.model_validate(
            {"summary": "Made it black and white.", "operations": [{"type": "set", "setting": "effects.saturation", "value": "0"}]}
        )

    monkeypatch.setattr(ai, "plan_edit", fake_plan)
    redis = await create_pool(AIWorkerSettings.redis_settings)
    JobQueue._pool = None
    document = EditDocument.model_validate({"segments": [{"id": "s", "start": 0, "end": 5}]})
    job_id = "f" * 32
    await redis.set(
        ai.request_key(job_id),
        json.dumps({"document": document.model_dump(), "instruction": "make it black and white", "duration": 5}),
    )
    try:
        await JobQueue.enqueue_job("ai_edit_job", job_id, _queue_name=ai.AI_EDIT_QUEUE)
        worker = Worker(
            functions=AIWorkerSettings.functions,
            queue_name=AIWorkerSettings.queue_name,
            redis_pool=redis,
            burst=True,
            poll_delay=0.05,
        )
        await worker.main()
        result = json.loads(await redis.get(ai.result_key(job_id)))
        assert result["status"] == "completed"
        assert result["summary"] == "Made it black and white."
        assert result["document"]["effects"]["saturation"] == 0
        # Nothing landed on the main video queue.
        assert await redis.zcard("supoclip_tasks") == 0
    finally:
        await redis.delete(ai.result_key(job_id), ai.request_key(job_id))
        await JobQueue.close_pool()
        await redis.aclose()
