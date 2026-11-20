"""A render whose worker died mid-run must not stay "processing" forever."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services import task_service as task_service_module
from src.services.task_service import TaskService


def _service(monkeypatch, rows, metadata, alive, update_changed=True):
    db = AsyncMock()
    result = MagicMock()
    result.__iter__ = lambda self: iter(rows)
    db.execute = AsyncMock(return_value=result)

    service = TaskService(db=db)
    service.task_repo.load_source_metadata = AsyncMock(return_value=metadata)
    service.task_repo.update_task_status = AsyncMock(return_value=update_changed)

    pool = AsyncMock()
    pool.exists = AsyncMock(return_value=alive)
    monkeypatch.setattr(
        task_service_module.JobQueue, "get_pool", AsyncMock(return_value=pool)
    )
    return service, db, pool


@pytest.mark.asyncio
async def test_dead_job_is_marked_error(monkeypatch):
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    service, db, pool = _service(
        monkeypatch,
        rows=[(task_id, "user-synthetic")],
        metadata={"processing_job_id": f"{task_id}:abc"},
        alive=False,
    )

    recovered = await service.sweep_stale_processing_tasks()

    assert recovered == [task_id]
    pool.exists.assert_awaited_once()
    update = service.task_repo.update_task_status.await_args
    assert update.args[1] == task_id
    assert update.args[2] == "error"
    assert update.kwargs["expected_statuses"] == ["processing"]


@pytest.mark.asyncio
async def test_live_job_is_left_alone(monkeypatch):
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    service, _db, _pool = _service(
        monkeypatch,
        rows=[(task_id, "user-synthetic")],
        metadata={"processing_job_id": f"{task_id}:abc"},
        alive=True,
    )

    recovered = await service.sweep_stale_processing_tasks()

    assert recovered == []
    service.task_repo.update_task_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_without_job_id_is_recovered(monkeypatch):
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    service, _db, pool = _service(
        monkeypatch,
        rows=[(task_id, "user-synthetic")],
        metadata={},
        alive=True,
    )

    recovered = await service.sweep_stale_processing_tasks()

    assert recovered == [task_id]
    pool.exists.assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_failure_skips_task(monkeypatch):
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    service, _db, pool = _service(
        monkeypatch,
        rows=[(task_id, "user-synthetic")],
        metadata={"processing_job_id": f"{task_id}:abc"},
        alive=False,
    )
    pool.exists.side_effect = RuntimeError("redis down")

    recovered = await service.sweep_stale_processing_tasks()

    assert recovered == []
    service.task_repo.update_task_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_race_with_finishing_worker_is_not_double_reported(monkeypatch):
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    service, _db, _pool = _service(
        monkeypatch,
        rows=[(task_id, "user-synthetic")],
        metadata={"processing_job_id": f"{task_id}:abc"},
        alive=False,
        update_changed=False,
    )

    recovered = await service.sweep_stale_processing_tasks()

    assert recovered == []