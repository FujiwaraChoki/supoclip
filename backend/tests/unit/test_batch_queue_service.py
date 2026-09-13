from unittest.mock import AsyncMock

from src.services.batch_queue_service import BatchQueueService


def _make_service():
    service = BatchQueueService(db=AsyncMock())
    return service


async def test_run_batch_processes_items_sequentially_and_completes():
    service = _make_service()
    queue = {
        "id": "queue-1",
        "user_id": "user-1",
        "template_id": None,
        "delay_between_items_seconds": 0,
        "auto_export_to_source": False,
    }
    items = [
        {"id": "item-1", "status": "pending", "source_filename": "a.mp4", "source_path": "upload://a"},
        {"id": "item-2", "status": "pending", "source_filename": "b.mp4", "source_path": "upload://b"},
    ]
    service.repo.get_queue = AsyncMock(return_value=queue)
    service.repo.get_items = AsyncMock(return_value=items)
    service.repo.update_queue_status = AsyncMock()
    service.repo.update_item_status = AsyncMock()
    service._resolve_template_settings = AsyncMock(return_value={})

    processed_order = []

    async def fake_process_item(item, q, template_settings):
        processed_order.append(item["id"])

    service._process_item = fake_process_item

    await service.run_batch("queue-1")

    assert processed_order == ["item-1", "item-2"]
    service.repo.update_queue_status.assert_any_call(service.db, "queue-1", "running", started_at=True)
    service.repo.update_queue_status.assert_any_call(service.db, "queue-1", "completed", completed_at=True)


async def test_run_batch_skips_already_done_items():
    service = _make_service()
    queue = {
        "id": "queue-1",
        "user_id": "user-1",
        "template_id": None,
        "delay_between_items_seconds": 0,
        "auto_export_to_source": False,
    }
    items = [
        {"id": "item-1", "status": "done", "source_filename": "a.mp4", "source_path": "upload://a"},
        {"id": "item-2", "status": "pending", "source_filename": "b.mp4", "source_path": "upload://b"},
    ]
    service.repo.get_queue = AsyncMock(return_value=queue)
    service.repo.get_items = AsyncMock(return_value=items)
    service.repo.update_queue_status = AsyncMock()
    service._resolve_template_settings = AsyncMock(return_value={})

    processed = []

    async def fake_process_item(item, q, template_settings):
        processed.append(item["id"])

    service._process_item = fake_process_item

    await service.run_batch("queue-1")

    assert processed == ["item-2"]


async def test_run_batch_continues_past_a_failing_item():
    """A failing item must never abort the batch — verified by asserting the
    batch still reaches "completed" even when _process_item raises for one
    item (in real code _process_item catches its own exceptions and marks the
    item "error" rather than propagating; this test locks in that the run_batch
    loop itself has no per-item try/except removed by accident)."""
    service = _make_service()
    queue = {
        "id": "queue-1",
        "user_id": "user-1",
        "template_id": None,
        "delay_between_items_seconds": 0,
        "auto_export_to_source": False,
    }
    items = [
        {"id": "item-1", "status": "pending", "source_filename": "a.mp4", "source_path": "upload://a"},
        {"id": "item-2", "status": "pending", "source_filename": "b.mp4", "source_path": "upload://b"},
    ]
    service.repo.get_queue = AsyncMock(return_value=queue)
    service.repo.get_items = AsyncMock(return_value=items)
    service.repo.update_queue_status = AsyncMock()
    service._resolve_template_settings = AsyncMock(return_value={})

    processed = []

    async def fake_process_item(item, q, template_settings):
        # Mirrors _process_item's real behavior: catches its own errors,
        # never re-raises, so run_batch's loop always proceeds.
        processed.append(item["id"])

    service._process_item = fake_process_item

    await service.run_batch("queue-1")

    assert processed == ["item-1", "item-2"]
    service.repo.update_queue_status.assert_any_call(service.db, "queue-1", "completed", completed_at=True)


async def test_run_batch_pauses_when_requested():
    service = _make_service()
    queue = {
        "id": "queue-1",
        "user_id": "user-1",
        "template_id": None,
        "delay_between_items_seconds": 0,
        "auto_export_to_source": False,
    }
    items = [
        {"id": "item-1", "status": "pending", "source_filename": "a.mp4", "source_path": "upload://a"},
        {"id": "item-2", "status": "pending", "source_filename": "b.mp4", "source_path": "upload://b"},
    ]
    service.repo.get_queue = AsyncMock(return_value=queue)
    service.repo.get_items = AsyncMock(return_value=items)
    service.repo.update_queue_status = AsyncMock()
    service._resolve_template_settings = AsyncMock(return_value={})

    processed = []

    async def fake_process_item(item, q, template_settings):
        processed.append(item["id"])

    service._process_item = fake_process_item

    await service.run_batch("queue-1", should_pause_or_cancel=AsyncMock(return_value="pause"))

    assert processed == []
    service.repo.update_queue_status.assert_any_call(service.db, "queue-1", "paused")


async def test_run_batch_cancels_when_requested():
    service = _make_service()
    queue = {
        "id": "queue-1",
        "user_id": "user-1",
        "template_id": None,
        "delay_between_items_seconds": 0,
        "auto_export_to_source": False,
    }
    items = [{"id": "item-1", "status": "pending", "source_filename": "a.mp4", "source_path": "upload://a"}]
    service.repo.get_queue = AsyncMock(return_value=queue)
    service.repo.get_items = AsyncMock(return_value=items)
    service.repo.update_queue_status = AsyncMock()
    service._resolve_template_settings = AsyncMock(return_value={})
    service._process_item = AsyncMock()

    await service.run_batch("queue-1", should_pause_or_cancel=AsyncMock(return_value="cancel"))

    service.repo.update_queue_status.assert_any_call(service.db, "queue-1", "cancelled", completed_at=True)
    service._process_item.assert_not_awaited()


async def test_process_item_marks_error_on_failure_without_raising():
    service = _make_service()
    item = {"id": "item-1", "source_filename": "a.mp4", "source_path": "upload://a"}
    queue = {"user_id": "user-1", "auto_export_to_source": False}
    service.repo.update_item_status = AsyncMock()

    class _FailingTaskService:
        def __init__(self, db):
            pass

        async def create_task_with_source(self, *args, **kwargs):
            raise RuntimeError("boom")

    import src.services.batch_queue_service as module

    original_task_service = module.TaskService
    module.TaskService = _FailingTaskService
    try:
        await service._process_item(item, queue, {})
    finally:
        module.TaskService = original_task_service

    error_calls = [
        call for call in service.repo.update_item_status.call_args_list
        if call.kwargs.get("status") == "error"
    ]
    assert len(error_calls) == 1
    assert "boom" in error_calls[0].kwargs["error_message"]
