"""A clip made without captions can be captioned later, in any template."""
import json
import shutil
import subprocess
from unittest.mock import AsyncMock

import pytest

from src.config import Config
from src.services.task_service import TaskService
from src.clip_source_map import save_clip_source_ranges


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
async def test_missing_caption_text_uses_the_clip_transcript_and_template_override(
    isolated_clip_edits, tmp_path, monkeypatch
):
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
    clip = {"id": "clip", "task_id": "task", "file_path": str(source), "filename": source.name,
            "start_time": "00:00", "end_time": "00:02", "duration": 1.5, "text": "original caption"}
    save_clip_source_ranges(source, [(0.1, 1.6)])
    service.clip_repo.get_clip_by_id = AsyncMock(side_effect=lambda *_: dict(clip))
    service.clip_repo.update_clip = AsyncMock()
    service.task_repo.get_task_by_id = AsyncMock(return_value={
        "id": "task", "source_type": "upload", "source_url": "upload://source.mp4",
        "caption_template": "default",
    })
    service.cache_repo.get_cache = AsyncMock(return_value={"video_path": str(source)})
    service._load_task_source_settings = AsyncMock(return_value={"output_format": "original"})
    monkeypatch.setattr("src.media.captions.emoji_rendering_supported", lambda: False)

    used = []
    from src import clip_editor
    real = clip_editor.overlay_custom_captions

    def spy(*args, **kwargs):
        used.append((args[2], kwargs.get("caption_template")))
        return real(*args, **kwargs)

    monkeypatch.setattr("src.services.clip_service.overlay_custom_captions", spy)

    await service.update_clip_captions(
        "task", "clip", None, "bottom", [], caption_template="typewriter"
    )

    assert used == [("original caption", "typewriter")]
    assert service.clip_repo.update_clip.await_args.args[-1] == "original caption"
