from pathlib import Path

import pytest

from src.clip_source_map import (
    load_clip_source_ranges,
    save_clip_source_ranges,
    split_source_ranges,
    trim_source_ranges,
)
from src.services.task_service import TaskService


def test_trim_source_ranges_preserves_non_contiguous_mapping():
    trimmed = trim_source_ranges([(10.0, 11.0), (13.0, 15.0)], 1.0, 0.0)

    assert trimmed == [(13.0, 15.0)]


def test_split_source_ranges_preserves_non_contiguous_mapping():
    first, second = split_source_ranges([(10.0, 11.0), (13.0, 15.0)], 1.0)

    assert first == [(10.0, 11.0)]
    assert second == [(13.0, 15.0)]


def test_save_and_load_clip_source_ranges(tmp_path):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"")

    save_clip_source_ranges(clip_path, [(10.0, 11.0), (13.0, 15.0)])

    assert load_clip_source_ranges(clip_path) == [(10.0, 11.0), (13.0, 15.0)]


class _FakeClipRepo:
    def __init__(self, clip: dict):
        self.clip = dict(clip)

    async def get_clip_by_id(self, _db, _clip_id: str):
        return dict(self.clip)

    async def update_clip(
        self,
        _db,
        _clip_id: str,
        filename: str,
        file_path: str,
        start_time: str,
        end_time: str,
        duration: float,
        text: str,
    ):
        self.clip.update(
            {
                "filename": filename,
                "file_path": file_path,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "text": text,
            }
        )


@pytest.mark.asyncio
async def test_trim_clip_uses_persisted_source_ranges(monkeypatch, tmp_path):
    input_path = tmp_path / "clip.mp4"
    input_path.write_bytes(b"input")
    save_clip_source_ranges(input_path, [(10.0, 11.0), (13.0, 15.0)])

    output_path = tmp_path / "trimmed.mp4"
    output_path.write_bytes(b"output")

    monkeypatch.setattr(
        "src.services.task_service.trim_clip_file",
        lambda *_args, **_kwargs: output_path,
    )

    repo = _FakeClipRepo(
        {
            "id": "clip-1",
            "task_id": "task-1",
            "file_path": str(input_path),
            "filename": "clip.mp4",
            "start_time": "00:10",
            "end_time": "00:20",
            "duration": 3.0,
            "text": "hello",
        }
    )

    service = TaskService(db=None)
    service.clip_repo = repo

    clip = await service.trim_clip("task-1", "clip-1", 1.0, 0.0)

    assert clip["start_time"] == "00:13"
    assert clip["end_time"] == "00:15"
    assert clip["duration"] == pytest.approx(2.0)
    assert load_clip_source_ranges(output_path) == [(13.0, 15.0)]


@pytest.mark.asyncio
async def test_regenerate_all_clips_reuses_persisted_source_ranges(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 11.0), (13.0, 15.0)])

    captured: dict[str, object] = {}

    class _FakeTaskRepo:
        async def get_task_by_id(self, _db, _task_id: str):
            return {
                "id": "task-1",
                "source_url": "upload://source.mp4",
                "source_type": "video_url",
            }

        async def update_task_clips(self, _db, _task_id: str, _clip_ids):
            return None

    class _FakeClipRepoWithTask:
        async def get_clips_by_task(self, _db, _task_id: str):
            return [
                {
                    "id": "clip-1",
                    "task_id": "task-1",
                    "file_path": str(clip_path),
                    "start_time": "00:10",
                    "end_time": "00:20",
                    "duration": 3.0,
                    "text": "hello",
                    "relevance_score": 0.8,
                    "reasoning": "test",
                    "virality_score": 1,
                    "hook_score": 1,
                    "engagement_score": 1,
                    "value_score": 1,
                    "shareability_score": 1,
                    "hook_type": "hook",
                }
            ]

        async def delete_clips_by_task(self, _db, _task_id: str):
            return None

    class _FakeVideoService:
        def resolve_local_video_path(self, _url: str) -> Path:
            return source_path

        async def create_video_clips(self, _video_path: Path, segments, *_args, **_kwargs):
            captured["segments"] = segments
            return []

    service = TaskService(db=None)
    async def fake_load_task_source_settings(_task_id: str):
        return {
            "output_format": "vertical",
            "add_subtitles": True,
            "cut_long_pauses": False,
            "pause_threshold_ms": 900,
            "remove_filler_words": False,
            "filtered_words": [],
        }

    monkeypatch.setattr(service, "_load_task_source_settings", fake_load_task_source_settings)
    service.task_repo = _FakeTaskRepo()
    service.clip_repo = _FakeClipRepoWithTask()
    service.video_service = _FakeVideoService()

    await service.regenerate_all_clips_for_task(
        "task-1",
        "TikTokSans-Regular",
        24,
        "#FFFFFF",
        "default",
        cleanup_settings={},
    )

    segments = captured["segments"]
    assert segments[0]["keep_ranges"] == [(10.0, 11.0), (13.0, 15.0)]


@pytest.mark.asyncio
async def test_regenerate_all_clips_recomputes_cleanup_from_source_ranges(
    monkeypatch, tmp_path
):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 11.0), (13.0, 15.0)])

    captured: dict[str, object] = {}

    class _FakeTaskRepo:
        async def get_task_by_id(self, _db, _task_id: str):
            return {
                "id": "task-1",
                "source_url": "upload://source.mp4",
                "source_type": "video_url",
            }

        async def update_task_clips(self, _db, _task_id: str, _clip_ids):
            return None

    class _FakeClipRepoWithTask:
        async def get_clips_by_task(self, _db, _task_id: str):
            return [
                {
                    "id": "clip-1",
                    "task_id": "task-1",
                    "file_path": str(clip_path),
                    "start_time": "00:10",
                    "end_time": "00:20",
                    "duration": 3.0,
                    "text": "hello",
                    "relevance_score": 0.8,
                    "reasoning": "test",
                    "virality_score": 1,
                    "hook_score": 1,
                    "engagement_score": 1,
                    "value_score": 1,
                    "shareability_score": 1,
                    "hook_type": "hook",
                }
            ]

        async def delete_clips_by_task(self, _db, _task_id: str):
            return None

    class _FakeVideoService:
        def resolve_local_video_path(self, _url: str) -> Path:
            return source_path

        async def create_video_clips(self, _video_path: Path, segments, *_args, **_kwargs):
            captured["segments"] = segments
            return []

    service = TaskService(db=None)

    async def fake_load_task_source_settings(_task_id: str):
        return {
            "output_format": "vertical",
            "add_subtitles": True,
            "cut_long_pauses": False,
            "pause_threshold_ms": 900,
            "remove_filler_words": False,
            "filtered_words": [],
        }

    monkeypatch.setattr(service, "_load_task_source_settings", fake_load_task_source_settings)
    service.task_repo = _FakeTaskRepo()
    service.clip_repo = _FakeClipRepoWithTask()
    service.video_service = _FakeVideoService()

    await service.regenerate_all_clips_for_task(
        "task-1",
        "TikTokSans-Regular",
        24,
        "#FFFFFF",
        "default",
        cleanup_settings={"cut_long_pauses": True},
    )

    segments = captured["segments"]
    assert "keep_ranges" not in segments[0]
    assert segments[0]["source_ranges"] == [(10.0, 11.0), (13.0, 15.0)]
