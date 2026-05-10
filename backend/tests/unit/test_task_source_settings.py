import pytest

from src.api.routes.tasks import _merge_task_source_metadata
from src.services.task_service import TaskService


class _FailingRedisClient:
    async def get(self, _key: str):
        raise RuntimeError("redis unavailable")

    async def close(self):
        return None


class _RedisClient:
    def __init__(self, payload: str):
        self.payload = payload

    async def get(self, _key: str):
        return self.payload

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_load_task_source_settings_falls_back_when_redis_fails(monkeypatch):
    monkeypatch.setattr(
        "src.services.task_service.redis.Redis",
        lambda **_kwargs: _FailingRedisClient(),
    )

    service = TaskService(db=None)
    settings = await service._load_task_source_settings("task-123")

    assert settings["output_format"] == "vertical"
    assert settings["add_subtitles"] is True
    assert settings["cut_long_pauses"] is False
    assert settings["pause_threshold_ms"] == 900
    assert settings["remove_filler_words"] is False
    assert settings["filtered_words"] == []


def test_merge_task_source_metadata_backfills_required_fields():
    merged = _merge_task_source_metadata(
        {},
        source_url="upload://demo.mp4",
        source_type="video_url",
        output_format="vertical",
        add_subtitles=True,
        cleanup_settings={
            "cut_long_pauses": True,
            "pause_threshold_ms": 900,
            "remove_filler_words": False,
            "filtered_words": [],
        },
    )

    assert merged == {
        "url": "upload://demo.mp4",
        "source_type": "video_url",
        "output_format": "vertical",
        "add_subtitles": True,
        "cut_long_pauses": True,
        "pause_threshold_ms": 900,
        "remove_filler_words": False,
        "filtered_words": [],
    }


def test_merge_task_source_metadata_preserves_existing_render_settings():
    merged = _merge_task_source_metadata(
        {
            "url": "upload://demo.mp4",
            "source_type": "video_url",
            "output_format": "original",
            "add_subtitles": False,
        },
        cleanup_settings={
            "cut_long_pauses": True,
            "pause_threshold_ms": 1200,
            "remove_filler_words": True,
            "filtered_words": ["basically"],
        },
    )

    assert merged["output_format"] == "original"
    assert merged["add_subtitles"] is False
    assert merged["cut_long_pauses"] is True
    assert merged["pause_threshold_ms"] == 1200
    assert merged["remove_filler_words"] is True
    assert merged["filtered_words"] == ["basically"]


def test_merge_task_source_metadata_accepts_smart_vertical_modes():
    merged = _merge_task_source_metadata(
        {},
        source_url="upload://demo.mp4",
        source_type="video_url",
        output_format="vertical_split",
        add_subtitles=True,
    )

    assert merged["output_format"] == "vertical_split"


@pytest.mark.asyncio
async def test_load_task_source_settings_accepts_speaker_pan_mode(monkeypatch):
    payload = '{"output_format": "vertical_pan", "add_subtitles": true}'
    monkeypatch.setattr(
        "src.services.task_service.redis.Redis",
        lambda **_kwargs: _RedisClient(payload),
    )

    service = TaskService(db=None)
    settings = await service._load_task_source_settings("task-123")

    assert settings["output_format"] == "vertical_pan"
