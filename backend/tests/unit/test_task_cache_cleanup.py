import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from src.config import Config, set_config_override
from src.services.video_service import VideoService
from src.services.task_service import TaskService


def test_video_service_get_cached_source_files_youtube(tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    set_config_override(config)
    try:
        video_id = "0Mw-i-TeJwo"
        main_video = tmp_path / f"{video_id}.mp4"
        transcription = tmp_path / f"{video_id}.transcription.mp3"
        checkpoint = tmp_path / f"{video_id}.transcript_checkpoint.json"
        unrelated = tmp_path / "other_video.mp4"

        main_video.write_bytes(b"x" * 1000)
        transcription.write_bytes(b"y" * 200)
        checkpoint.write_bytes(b"z" * 50)
        unrelated.write_bytes(b"other")

        files = VideoService.get_cached_source_files(
            f"https://www.youtube.com/watch?v={video_id}", "youtube"
        )
        assert len(files) == 3
        assert main_video in files
        assert transcription in files
        assert checkpoint in files
        assert unrelated not in files

        info = VideoService.get_cached_source_info(
            f"https://www.youtube.com/watch?v={video_id}", "youtube"
        )
        assert info["has_cached_video"] is True
        assert info["file_count"] == 3
        assert info["total_size_bytes"] == 1250

        # Clear cache
        result = VideoService.clear_cached_source_files(
            f"https://www.youtube.com/watch?v={video_id}", "youtube"
        )
        assert result["cleared_files_count"] == 3
        assert result["freed_bytes"] == 1250
        assert not main_video.exists()
        assert not transcription.exists()
        assert not checkpoint.exists()
        assert unrelated.exists()

        # Check info after clearing
        info_after = VideoService.get_cached_source_info(
            f"https://www.youtube.com/watch?v={video_id}", "youtube"
        )
        assert info_after["has_cached_video"] is False
        assert info_after["file_count"] == 0
        assert info_after["total_size_bytes"] == 0
    finally:
        set_config_override(None)


def test_video_service_get_cached_source_files_upload(tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    set_config_override(config)
    try:
        uploads_dir = tmp_path / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        upload_file = uploads_dir / "user_sample_123.mp4"
        upload_audio = uploads_dir / "user_sample_123.transcription.mp3"
        upload_file.write_bytes(b"u" * 500)
        upload_audio.write_bytes(b"a" * 100)

        info = VideoService.get_cached_source_info(
            "upload://user_sample_123.mp4", "upload"
        )
        assert info["has_cached_video"] is True
        assert info["file_count"] == 2
        assert info["total_size_bytes"] == 600

        result = VideoService.clear_cached_source_files(
            "upload://user_sample_123.mp4", "upload"
        )
        assert result["cleared_files_count"] == 2
        assert result["freed_bytes"] == 600
        assert not upload_file.exists()
        assert not upload_audio.exists()
    finally:
        set_config_override(None)


@pytest.mark.asyncio
async def test_task_service_clear_task_cache(tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    set_config_override(config)
    try:
        video_id = "test_vid_abc"
        video_file = tmp_path / f"{video_id}.mp4"
        video_file.write_bytes(b"v" * 800)

        service = TaskService(db=AsyncMock())
        service.task_repo.get_task_by_id = AsyncMock(
            return_value={
                "id": "task-cache-1",
                "source_url": f"https://www.youtube.com/watch?v={video_id}",
                "source_type": "youtube",
            }
        )

        info = await service.get_task_cache_info("task-cache-1")
        assert info["has_cached_video"] is True
        assert info["total_size_bytes"] == 800

        clear_res = await service.clear_task_cache("task-cache-1")
        assert clear_res["cleared_files_count"] == 1
        assert clear_res["freed_bytes"] == 800
        assert not video_file.exists()
    finally:
        set_config_override(None)
