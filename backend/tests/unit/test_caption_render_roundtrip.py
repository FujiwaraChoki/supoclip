"""Exercise real ffmpeg rendering without a database or provider API."""
from pathlib import Path
import json
import shutil
import subprocess
from unittest.mock import AsyncMock

import pytest

from src.config import Config
from src.services.task_service import TaskService
from src.clip_source_map import load_clip_caption_settings, save_clip_source_ranges


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
async def test_caption_saves_render_from_source_and_keep_exact_boundaries(isolated_clip_edits, tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
    ], check=True, capture_output=True)
    source.with_suffix(".transcript_cache.json").write_text(json.dumps({
        "version": 2, "words": [
            {"text": "original", "start": 200, "end": 500},
            {"text": "caption", "start": 700, "end": 1100},
        ], "utterances": [], "text": "original caption",
    }))
    config = Config()
    config.temp_dir = str(tmp_path)
    service = TaskService(AsyncMock(), config=config)
    clip = {"id": "clip", "task_id": "task", "file_path": str(source),
            "filename": source.name, "start_time": "00:00", "end_time": "00:02", "duration": 1.5}
    save_clip_source_ranges(source, [(0.1, 1.6)])
    service.clip_repo.get_clip_by_id = AsyncMock(side_effect=lambda *_: dict(clip))
    async def update(_db, _id, filename, file_path, *_):
        clip.update(filename=filename, file_path=file_path)
    service.clip_repo.update_clip = AsyncMock(side_effect=update)
    service.task_repo.get_task_by_id = AsyncMock(return_value={
        "id": "task", "source_type": "upload", "source_url": "upload://source.mp4",
        "caption_template": "minimal",
    })
    service.cache_repo.get_cache = AsyncMock(return_value={"video_path": str(source)})
    service._load_task_source_settings = AsyncMock(return_value={"output_format": "original"})
    monkeypatch.setattr("src.media.captions.emoji_rendering_supported", lambda: False)

    await service.update_clip_captions("task", "clip", "first edit", "bottom", [], font_size=18, position_y=0.7)
    first_output = Path(clip["file_path"])
    assert first_output.exists()
    assert load_clip_caption_settings(first_output)["font_size"] == 18
    # Saving an empty caption must remove the previous text, rather than copying
    # or burning another caption layer onto the first edited output.
    await service.update_clip_captions("task", "clip", "", "bottom", [])
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", clip["file_path"],
    ], check=True, capture_output=True, text=True)
    assert float(json.loads(result.stdout)["format"]["duration"]) == pytest.approx(1.5, abs=0.08)
    # A solid source frame remains uniform after clearing captions.
    frame = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", "0.3", "-i", clip["file_path"],
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ], check=True, capture_output=True).stdout
    assert len(set(frame[0::3])) < 5
    assert len(set(frame[1::3])) < 5
