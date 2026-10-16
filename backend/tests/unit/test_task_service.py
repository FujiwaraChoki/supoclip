from src.services import clip_service as clip_service_module
from datetime import datetime, timezone
from contextlib import nullcontext
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.config import Config
from src.ai import TRANSCRIPT_ANALYSIS_CACHE_VERSION
from src.services import task_service as task_service_module
from src.services.task_service import TaskService


@pytest.mark.asyncio
async def test_create_task_with_source_creates_queued_task(monkeypatch):
    service = TaskService(db=AsyncMock())
    service.task_repo.user_exists = AsyncMock(return_value=True)
    service.source_repo.create_source = AsyncMock(return_value="source-1")
    service.task_repo.create_task = AsyncMock(return_value="task-1")
    monkeypatch.setattr(
        service.video_service,
        "determine_source_type",
        lambda _url: "youtube",
    )
    service.video_service.get_video_title = AsyncMock(return_value="Seeded title")

    task_id = await service.create_task_with_source(
        user_id="user-1",
        url="https://www.youtube.com/watch?v=demo",
    )

    assert task_id == "task-1"
    service.task_repo.create_task.assert_awaited_once()

@pytest.mark.asyncio
async def test_create_task_with_source_requires_existing_user():
    service = TaskService(db=AsyncMock())
    service.task_repo.user_exists = AsyncMock(return_value=False)

    with pytest.raises(ValueError):
        await service.create_task_with_source(
            user_id="missing-user",
            url="https://example.com/video.mp4",
        )


def build_clip_result() -> dict:
    return {
        "filename": "clip-1.mp4",
        "path": "/tmp/clip-1.mp4",
        "start_time": "00:00",
        "end_time": "00:10",
        "duration": 10.0,
        "text": "Hook text",
        "relevance_score": 0.95,
        "reasoning": "Strong hook",
    }


def build_task_service() -> TaskService:
    config = Config()
    config.app_base_url = "http://localhost:3107"
    config.aws_region = "us-east-1"
    config.aws_access_key_id = "AKIATEST"
    config.aws_secret_access_key = "secret-test"
    config.ses_from_email = "SupoClip <noreply@example.com>"
    service = TaskService(db=AsyncMock(), config=config)
    service._load_performance_context = AsyncMock(return_value=None)
    service._processing_transaction = lambda _task_id: nullcontext()
    service._task_run_guard = lambda _task_id: nullcontext()
    service.task_repo.get_task_by_id = AsyncMock(return_value={"status": "queued"})
    service.cache_repo.get_cache = AsyncMock(return_value=None)
    service.cache_repo.upsert_cache = AsyncMock()
    service.task_repo.update_task_runtime_metadata = AsyncMock()
    service.task_repo.update_task_status = AsyncMock()
    service.task_repo.update_task_clips = AsyncMock()
    service.task_repo.get_task_notification_context = AsyncMock(return_value=None)
    service.clip_repo.delete_clips_by_task = AsyncMock(return_value=0)
    service.clip_repo.create_clip = AsyncMock(return_value="clip-1")
    service.video_service.create_single_clip = AsyncMock(return_value=build_clip_result())
    service.video_service.apply_single_transition = AsyncMock(
        side_effect=lambda _prev_clip_path, clip_info, _index, _clips_output_dir: clip_info
    )
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "clips": [build_clip_result()],
            "segments_to_render": [{"start": 0, "end": 10}],
            "video_path": "/tmp/source.mp4",
            "segments": [],
            "summary": None,
            "key_topics": [],
            "transcript": "Transcript",
            "analysis_json": "{}",
        }
    )
    return service


def test_cache_key_includes_analysis_prompt_version():
    url = "https://www.youtube.com/watch?v=demo"
    cache_key = TaskService._build_cache_key(
        url,
        "youtube",
        "fast",
    )
    expected = hashlib.sha256(
        f"youtube|fast|{TRANSCRIPT_ANALYSIS_CACHE_VERSION}|{url}".encode("utf-8")
    ).hexdigest()

    assert cache_key == expected


@pytest.mark.asyncio
async def test_update_clip_captions_passes_stored_task_style(isolated_clip_edits, monkeypatch, tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    service = TaskService(db=AsyncMock(), config=config)
    input_path = tmp_path / "clip.mp4"
    input_path.write_bytes(b"clip")
    output_path = tmp_path / "clips" / "edited.mp4"
    output_path.parent.mkdir()
    clip = {
        "id": "clip-1",
        "task_id": "task-1",
        "file_path": str(input_path),
        "start_time": "00:10",
        "end_time": "00:12",
        "duration": 2.0,
        "hook_title": "Keep this headline style",
    }
    service.clip_repo.get_clip_by_id = AsyncMock(
        side_effect=[clip, {**clip, "file_path": str(output_path)}]
    )
    service.clip_repo.update_clip = AsyncMock()
    service.task_repo.get_task_by_id = AsyncMock(
        return_value={
            "id": "task-1",
            "source_url": "upload://source.mp4",
            "source_type": "upload",
            "processing_mode": "quality",
            "font_family": "Inter",
            "font_size": 48,
            "font_color": "#123456",
            "caption_template": "minimal",
        }
    )
    service.cache_repo.get_cache = AsyncMock(
        return_value={"video_path": str(tmp_path / "source.mp4")}
    )
    captured = {}

    def fake_overlay(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return output_path

    monkeypatch.setattr(clip_service_module, "overlay_custom_captions", fake_overlay)
    (tmp_path / "source.mp4").write_bytes(b"source")
    service._load_task_source_settings = AsyncMock(return_value={"output_format": "original"})
    rendered = []
    def fake_render(*args, **kwargs):
        rendered.append((args, kwargs))
        args[3].write_bytes(b"clean")
        return True
    monkeypatch.setattr(clip_service_module, "create_optimized_clip", fake_render)

    await service.update_clip_captions(
        "task-1", "clip-1", "edited caption", "middle", ["edited"]
    )

    assert captured["kwargs"] == {
        "font_family": "Inter",
        "font_size": 48,
        "font_color": "#123456",
        "caption_template": "minimal",
        "transcript_video_path": tmp_path / "source.mp4",
        "source_ranges": [(10.0, 12.0)],
        "position_y": None,
    }
    assert captured["args"][0].name == "clean.mp4"
    assert rendered[0][0][0] == tmp_path / "source.mp4"
    assert rendered[0][1]["add_subtitles"] is False
    assert rendered[0][1]["extend_to_sentence"] is False
    assert rendered[0][1]["hook_title"] == "Keep this headline style"
    assert rendered[0][1]["font_family"] == "Inter"
    assert rendered[0][1]["font_size"] == 48
    assert rendered[0][1]["font_color"] == "#123456"
    assert rendered[0][1]["caption_template"] == "minimal"


@pytest.mark.asyncio
async def test_process_task_fails_when_no_clip_segments_are_selected():
    service = build_task_service()
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "clips": [],
            "segments_to_render": [],
            "video_path": "/tmp/source.mp4",
            "segments": [],
            "summary": None,
            "key_topics": [],
            "transcript": "Transcript",
            "analysis_json": '{"most_relevant_segments":[]}',
        }
    )

    with pytest.raises(ValueError, match="No usable clip segments"):
        await service.process_task(
            task_id="task-1",
            url="https://www.youtube.com/watch?v=demo",
            source_type="youtube",
        )

    service.cache_repo.upsert_cache.assert_awaited_once()
    assert service.cache_repo.upsert_cache.await_args.kwargs["analysis_json"] is None
    service.task_repo.update_task_status.assert_any_await(
        service.db,
        "task-1",
        "error",
        progress=0,
        progress_message="No usable clip segments were selected for this video.",
        expected_statuses=["queued", "processing"],
    )
    service.clip_repo.create_clip.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_uses_scale_youtube_duration_allowance(monkeypatch):
    service = build_task_service()
    service.config.max_video_duration = 5400
    service.config.scale_youtube_max_video_duration = 10800

    class FakeBillingService:
        def __init__(self, *_args, **_kwargs):
            pass

        async def get_usage_summary(self, _user_id):
            return {"plan": "scale", "subscription_status": "active"}

    monkeypatch.setattr(task_service_module, "BillingService", FakeBillingService)

    await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
        user_id="scale-user",
    )

    assert (
        service.video_service.process_video_complete.await_args.kwargs[
            "max_video_duration"
        ]
        == 10800
    )


@pytest.mark.asyncio
async def test_process_task_sends_completion_email_when_enabled(monkeypatch):
    service = build_task_service()
    service.task_repo.get_task_notification_context = AsyncMock(
        return_value={
            "notify_on_completion": True,
            "completion_notification_sent_at": None,
            "source_title": "Demo video",
            "user_email": "user@example.com",
            "user_name": "Demo User",
            "user_first_name": "Demo",
        }
    )
    service.task_repo.mark_completion_notification_sent = AsyncMock(return_value=True)
    send_task_completed_email = AsyncMock(return_value={"id": "email-1"})

    class FakeTaskCompletionEmailService:
        def __init__(self, config):
            self.config = config

        @property
        def is_configured(self) -> bool:
            return True

        async def send_task_completed_email(self, **kwargs):
            return await send_task_completed_email(**kwargs)

    monkeypatch.setattr(
        task_service_module,
        "TaskCompletionEmailService",
        FakeTaskCompletionEmailService,
    )

    result = await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    assert result["clips_count"] == 1
    send_task_completed_email.assert_awaited_once()
    service.task_repo.mark_completion_notification_sent.assert_awaited_once_with(
        service.db, "task-1"
    )


@pytest.mark.asyncio
async def test_process_task_skips_completion_email_when_disabled(monkeypatch):
    service = build_task_service()
    service.task_repo.get_task_notification_context = AsyncMock(
        return_value={
            "notify_on_completion": False,
            "completion_notification_sent_at": None,
            "source_title": "Demo video",
            "user_email": "user@example.com",
            "user_name": "Demo User",
            "user_first_name": "Demo",
        }
    )
    service.task_repo.mark_completion_notification_sent = AsyncMock(return_value=True)
    send_task_completed_email = AsyncMock()

    class FakeTaskCompletionEmailService:
        def __init__(self, config):
            self.config = config

        @property
        def is_configured(self) -> bool:
            return True

        async def send_task_completed_email(self, **kwargs):
            return await send_task_completed_email(**kwargs)

    monkeypatch.setattr(
        task_service_module,
        "TaskCompletionEmailService",
        FakeTaskCompletionEmailService,
    )

    await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    send_task_completed_email.assert_not_awaited()
    service.task_repo.mark_completion_notification_sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_keeps_generated_clips_standalone():
    service = build_task_service()
    service.task_repo.get_task_notification_context = AsyncMock(
        return_value={
            "notify_on_completion": False,
            "completion_notification_sent_at": None,
            "source_title": "Demo video",
            "user_email": "user@example.com",
            "user_name": "Demo User",
            "user_first_name": "Demo",
        }
    )
    service.video_service.create_single_clip = AsyncMock(
        side_effect=[
            {
                **build_clip_result(),
                "filename": "clip-1.mp4",
                "path": "/tmp/clip-1.mp4",
                "duration": 10.0,
            },
            {
                **build_clip_result(),
                "filename": "clip-2.mp4",
                "path": "/tmp/clip-2.mp4",
                "start_time": "00:10",
                "end_time": "00:20",
                "duration": 10.0,
            },
        ]
    )
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "clips": [build_clip_result(), build_clip_result()],
            "segments_to_render": [
                {"start_time": "00:00", "end_time": "00:10"},
                {"start_time": "00:10", "end_time": "00:20"},
            ],
            "video_path": "/tmp/source.mp4",
            "segments": [],
            "summary": None,
            "key_topics": [],
            "transcript": "Transcript",
            "analysis_json": "{}",
        }
    )

    result = await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    assert result["clips_count"] == 2
    service.video_service.apply_single_transition.assert_not_awaited()
    saved_paths = [
        call.kwargs["file_path"]
        for call in service.clip_repo.create_clip.await_args_list
    ]
    assert saved_paths == ["/tmp/clip-1.mp4", "/tmp/clip-2.mp4"]


@pytest.mark.asyncio
async def test_process_task_clears_existing_clips_before_rendering():
    service = build_task_service()
    service.task_repo.get_task_notification_context = AsyncMock(
        return_value={
            "notify_on_completion": False,
            "completion_notification_sent_at": None,
            "source_title": "Demo video",
            "user_email": "user@example.com",
            "user_name": "Demo User",
            "user_first_name": "Demo",
        }
    )

    await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    service.clip_repo.delete_clips_by_task.assert_awaited_once_with(service.db, "task-1")
    assert any(
        call.args == (service.db, "task-1", [])
        for call in service.task_repo.update_task_clips.await_args_list
    )


@pytest.mark.asyncio
async def test_process_task_ignores_completion_email_failures(monkeypatch):
    service = build_task_service()
    service.task_repo.get_task_notification_context = AsyncMock(
        return_value={
            "notify_on_completion": True,
            "completion_notification_sent_at": None,
            "source_title": "Demo video",
            "user_email": "user@example.com",
            "user_name": "Demo User",
            "user_first_name": "Demo",
        }
    )
    service.task_repo.mark_completion_notification_sent = AsyncMock(return_value=True)
    send_task_completed_email = AsyncMock(side_effect=RuntimeError("email failed"))

    class FakeTaskCompletionEmailService:
        def __init__(self, config):
            self.config = config

        @property
        def is_configured(self) -> bool:
            return True

        async def send_task_completed_email(self, **kwargs):
            return await send_task_completed_email(**kwargs)

    monkeypatch.setattr(
        task_service_module,
        "TaskCompletionEmailService",
        FakeTaskCompletionEmailService,
    )

    result = await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    assert result["clips_count"] == 1
    send_task_completed_email.assert_awaited_once()
    service.task_repo.mark_completion_notification_sent.assert_not_awaited()
    assert any(
        call.kwargs.get("completed_at") is not None
        for call in service.task_repo.update_task_runtime_metadata.await_args_list
    )


@pytest.mark.asyncio
async def test_process_task_skips_completion_email_when_already_sent(monkeypatch):
    service = build_task_service()
    service.task_repo.get_task_notification_context = AsyncMock(
        return_value={
            "notify_on_completion": True,
            "completion_notification_sent_at": datetime.now(timezone.utc),
            "source_title": "Demo video",
            "user_email": "user@example.com",
            "user_name": "Demo User",
            "user_first_name": "Demo",
        }
    )
    service.task_repo.mark_completion_notification_sent = AsyncMock(return_value=True)
    send_task_completed_email = AsyncMock()

    class FakeTaskCompletionEmailService:
        def __init__(self, config):
            self.config = config

        @property
        def is_configured(self) -> bool:
            return True

        async def send_task_completed_email(self, **kwargs):
            return await send_task_completed_email(**kwargs)

    monkeypatch.setattr(
        task_service_module,
        "TaskCompletionEmailService",
        FakeTaskCompletionEmailService,
    )

    await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    send_task_completed_email.assert_not_awaited()
    service.task_repo.mark_completion_notification_sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_processing_rolls_back_failed_write_before_recording_error():
    service = build_task_service()
    transaction_failed = False

    async def fail_insert(*args, **kwargs):
        nonlocal transaction_failed
        transaction_failed = True
        raise RuntimeError("clip insert failed")

    async def rollback():
        nonlocal transaction_failed
        transaction_failed = False

    async def check_status(*args, **kwargs):
        assert not transaction_failed, "Cannot write status in an aborted transaction"

    service.clip_repo.create_clip.side_effect = fail_insert
    service.db.rollback.side_effect = rollback
    service.task_repo.update_task_status.side_effect = check_status
    with pytest.raises(RuntimeError, match="clip insert failed"):
        await service.process_task(task_id="task-1", url="upload://test.mp4", source_type="upload", user_id="user-1")
    service.db.rollback.assert_awaited_once()
    assert service.task_repo.update_task_status.await_args.args[2] == "error"



@pytest.mark.asyncio
async def test_merge_rejects_duplicate_ids_before_modifying_clips(isolated_clip_edits):
    service = build_task_service()
    service.clip_repo.get_clip_by_id = AsyncMock()
    service.clip_repo.delete_clip = AsyncMock()
    with pytest.raises(ValueError, match="distinct"):
        await service.merge_clips("task-1", ["clip-1", "clip-1"])
    service.clip_repo.get_clip_by_id.assert_not_awaited()
    service.clip_repo.delete_clip.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_fails_when_every_clip_render_fails(tmp_path):
    service = build_task_service()
    service.config.temp_dir = str(tmp_path)
    service.video_service.create_single_clip = AsyncMock(return_value=None)
    callback = AsyncMock()

    with pytest.raises(ValueError, match="No clips could be rendered"):
        await service.process_task(
            task_id="task-1",
            url="upload://source.mp4",
            source_type="video_url",
            progress_callback=callback,
        )

    service.clip_repo.create_clip.assert_not_awaited()
    service.task_repo.get_task_notification_context.assert_not_awaited()
    assert not any(
        call.args[2] == "completed"
        for call in service.task_repo.update_task_status.await_args_list
    )
    assert not any(call.args[2] == "completed" for call in callback.await_args_list)
    callback.assert_any_await(0, "No clips could be rendered. Please try again.", "error")
    service.task_repo.update_task_status.assert_any_await(
        service.db, "task-1", "error", progress=0,
        progress_message="No clips could be rendered. Please try again.",
        expected_statuses=["queued", "processing"],
    )


@pytest.mark.asyncio
async def test_process_task_keeps_successful_output_when_one_render_fails(tmp_path):
    service = build_task_service()
    service.config.temp_dir = str(tmp_path)
    service.video_service.process_video_complete.return_value["segments_to_render"] *= 2
    service.video_service.create_single_clip = AsyncMock(
        side_effect=[None, build_clip_result()]
    )

    result = await service.process_task(
        task_id="task-1", url="upload://source.mp4", source_type="video_url"
    )

    assert result["clips_count"] == 1
    service.clip_repo.create_clip.assert_awaited_once()
    service.task_repo.update_task_status.assert_any_await(
        service.db, "task-1", "completed", progress=100,
        progress_message="Complete!", expected_statuses=["processing"],
    )


@pytest.mark.asyncio
async def test_failure_notification_error_preserves_render_failure(tmp_path):
    service = build_task_service()
    service.config.temp_dir = str(tmp_path)
    service.video_service.create_single_clip = AsyncMock(return_value=None)

    async def notify(progress, message, status):
        if status == "error":
            raise RuntimeError("Redis unavailable")

    with pytest.raises(ValueError, match="No clips could be rendered"):
        await service.process_task(
            task_id="task-1", url="upload://source.mp4", source_type="video_url",
            progress_callback=notify,
        )

    assert service.task_repo.update_task_status.await_args.args[2] == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("job_exists,redis_error,expected_status", [
    (True, False, "queued"), (False, True, "queued"), (False, False, "error"),
])
async def test_old_queued_task_keeps_reservation_while_job_exists_or_redis_unknown(
    monkeypatch, job_exists, redis_error, expected_status
):
    from datetime import timedelta
    from types import SimpleNamespace
    service = TaskService(db=AsyncMock())
    now = datetime.now(timezone.utc)
    task = {"id": "queued-task", "status": "queued", "created_at": now - timedelta(hours=1)}
    service.task_repo.get_task_by_id = AsyncMock(side_effect=[task, {**task, "status": "error"}])
    service.task_repo.update_task_status = AsyncMock()
    service.clip_repo.get_clips_by_task = AsyncMock(return_value=[])
    service._load_task_source_settings = AsyncMock(return_value={})
    service.task_repo.load_source_metadata = AsyncMock(return_value={"processing_job_id": "job-1"})
    pool = SimpleNamespace(exists=AsyncMock(
        return_value=job_exists, side_effect=RuntimeError("Redis down") if redis_error else None
    ))
    monkeypatch.setattr(task_service_module.JobQueue, "get_pool", AsyncMock(return_value=pool))
    result = await service.get_task_with_clips(task["id"])
    assert result["status"] == expected_status
    if expected_status == "queued":
        service.task_repo.update_task_status.assert_not_awaited()
    else:
        assert service.task_repo.update_task_status.await_args.kwargs["expected_statuses"] == ["queued"]


@pytest.mark.asyncio
@pytest.mark.parametrize("age_hours,missing", [(1, False), (25, True)])
async def test_legacy_queue_reservations_keep_full_arq_retention_window(age_hours, missing):
    from datetime import timedelta
    service = TaskService(db=AsyncMock())
    service.task_repo.load_source_metadata = AsyncMock(return_value={})
    task = {"id": "legacy", "created_at": datetime.now(timezone.utc) - timedelta(hours=age_hours)}
    assert await service._queued_job_is_missing(task) is missing


@pytest.mark.asyncio
async def test_performance_context_uses_its_own_session(monkeypatch):
    from src import database
    from src.services import social_service

    sessions = []

    class Session:
        async def __aenter__(self):
            sessions.append(self)
            return self

        async def __aexit__(self, *exc):
            self.closed = True

    monkeypatch.setattr(database, "AsyncSessionLocal", Session)
    service = TaskService(db=AsyncMock(), config=Config())

    async def missing_tables(self, user_id):
        raise RuntimeError('relation "social_posts" does not exist')

    monkeypatch.setattr(social_service.SocialService, "build_performance_context", missing_tables)
    assert await service._load_performance_context("user-1") is None
    assert sessions and sessions[0].closed
    # The task's own session never saw the failing query.
    service.db.execute.assert_not_awaited()
    service.db.rollback.assert_not_awaited()

    async def context(self, user_id):
        assert self.db is sessions[-1]
        return "history"

    monkeypatch.setattr(social_service.SocialService, "build_performance_context", context)
    assert await service._load_performance_context("user-1") == "history"
    assert await service._load_performance_context(None) is None


@pytest.mark.asyncio
async def test_personalized_run_is_not_reported_as_a_cache_hit():
    service = build_task_service()
    service.cache_repo.get_cache = AsyncMock(
        return_value={"transcript_text": "Transcript", "analysis_json": "{}"}
    )
    service._load_performance_context = AsyncMock(return_value="history")
    await service.process_task(task_id="task-1", url="upload://test.mp4", source_type="upload", user_id="user-1")
    first = service.task_repo.update_task_runtime_metadata.await_args_list[0]
    assert first.kwargs["cache_hit"] is False
    assert service.video_service.process_video_complete.await_args.kwargs["cached_analysis_json"] is None

    service = build_task_service()
    service.cache_repo.get_cache = AsyncMock(
        return_value={"transcript_text": "Transcript", "analysis_json": "{}"}
    )
    await service.process_task(task_id="task-1", url="upload://test.mp4", source_type="upload", user_id="user-1")
    assert service.task_repo.update_task_runtime_metadata.await_args_list[0].kwargs["cache_hit"] is True
