import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.video_service import VideoService


@pytest.mark.asyncio
async def test_create_video_clips_renders_bounded_in_parallel_and_ordered(
    monkeypatch, tmp_path
):
    """create_video_clips fans create_single_clip out concurrently, but caps how many
    run at once, keeps results in segment order, and drops failed renders."""
    monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", "2")
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))

    active = 0
    max_active = 0
    lock = asyncio.Lock()

    async def fake_single_clip(video_path, segment, index, output_dir, *args, **kwargs):
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
        await asyncio.sleep(0.02)  # simulate render work so overlap is observable
        async with lock:
            active -= 1
        if index == 2:
            return None  # simulate one failed render
        return {"clip_id": index + 1, "filename": f"clip_{index + 1}.mp4"}

    # Class access returns the plain function (no binding), matching the static call site.
    monkeypatch.setattr(VideoService, "create_single_clip", fake_single_clip)

    segments = [{"start_time": "00:00", "end_time": "00:10"} for _ in range(5)]
    clips = await VideoService.create_video_clips(tmp_path / "src.mp4", segments)

    # Concurrency was actually used (>1) but capped at the configured 2.
    assert max_active == 2
    # 5 segments, index 2 failed -> 4 clips, preserved in segment order.
    assert [clip["clip_id"] for clip in clips] == [1, 2, 4, 5]


@pytest.mark.asyncio
async def test_create_video_clips_concurrency_one_is_sequential(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", "1")
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))

    active = 0
    max_active = 0
    lock = asyncio.Lock()

    async def fake_single_clip(video_path, segment, index, output_dir, *args, **kwargs):
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        async with lock:
            active -= 1
        return {"clip_id": index + 1}

    monkeypatch.setattr(VideoService, "create_single_clip", fake_single_clip)
    segments = [{"start_time": "00:00", "end_time": "00:10"} for _ in range(3)]
    clips = await VideoService.create_video_clips(tmp_path / "src.mp4", segments)

    assert max_active == 1  # concurrency=1 -> strictly sequential
    assert [clip["clip_id"] for clip in clips] == [1, 2, 3]
