"""Unit tests for render pipeline performance optimizations."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import cv2
import numpy as np
import pytest

from src.media import common, ffmpeg, reframing, screen_text
from tests.unit.test_task_service import build_task_service


class _OkResult:
    returncode = 0
    stdout = ""
    stderr = ""


def test_encode_args_use_fast_presets_and_bounded_threads():
    final_args = ffmpeg.build_final_video_encode_args()
    assert final_args[final_args.index("-preset") + 1] == common.FINAL_VIDEO_PRESET
    assert common.FINAL_VIDEO_PRESET == "veryfast"
    assert "-threads" in final_args
    assert int(final_args[final_args.index("-threads") + 1]) == common.FFMPEG_ENCODE_THREADS

    inter_args = ffmpeg.build_intermediate_video_encode_args()
    assert inter_args[inter_args.index("-preset") + 1] == common.INTERMEDIATE_VIDEO_PRESET
    assert common.INTERMEDIATE_VIDEO_PRESET == "ultrafast"
    assert "-threads" in inter_args


def test_ffprobe_cache_reuses_result_and_invalidates_on_file_change(tmp_path):
    clip = tmp_path / "sample.mp4"
    clip.write_bytes(b"v1")

    calls = 0

    def fake_run(cmd, timeout=60):
        nonlocal calls
        calls += 1
        res = _OkResult()
        res.stdout = '{"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}'
        return res

    with patch.object(ffmpeg, "run_ffmpeg_command", side_effect=fake_run):
        assert ffmpeg.ffprobe_video_size(clip) == (1920, 1080)
        assert ffmpeg.ffprobe_video_size(clip) == (1920, 1080)
        assert calls == 1

        # Changing file size invalidates the stat-keyed cache entry.
        clip.write_bytes(b"v2-longer-content")
        assert ffmpeg.ffprobe_video_size(clip) == (1920, 1080)
        assert calls == 2


def test_short_side_scale_filter_inline_in_final_render(tmp_path):
    assert ffmpeg.short_side_scale_filter(1080, 1920, 720) == "scale=720:1280:flags=bicubic,setsar=1"
    assert ffmpeg.short_side_scale_filter(720, 1280, 720) is None
    assert ffmpeg.short_side_scale_filter(1080, 1920, None) is None

    commands = []

    def fake_run(cmd, timeout=900):
        commands.append(cmd)
        return _OkResult()

    with (
        patch.object(reframing, "ffprobe_video_size", return_value=(1920, 1080)),
        patch.object(reframing, "ffprobe_has_audio", return_value=True),
        patch.object(reframing, "run_ffmpeg_command", side_effect=fake_run),
    ):
        ok, _, _ = reframing.render_reframed_clip_ffmpeg(
            tmp_path / "in.mp4",
            tmp_path / "out.mp4",
            "vertical",
            vertical_plan=("crop=608:1080:656:0,scale=1080:1920:flags=lanczos,setsar=1", "vf"),
            max_short_side=720,
        )

    assert ok is True
    vf = commands[0][commands[0].index("-vf") + 1]
    assert "scale=720:1280:flags=bicubic,setsar=1" in vf


def test_compositor_uses_quarter_res_gated_background_blur():
    graph = reframing.build_vertical_compositor_filter(
        f"crop=608:1080:0:0,{reframing.VERTICAL_SCALE_CHAIN}",
        [(0.0, 5.0)],
        [(5.0, 10.0)],
    )
    assert "scale=270:480:" in graph
    assert "gblur=sigma=7:steps=2:enable=" in graph


def test_build_ocr_page_png_stacks_three_grayscale_variants():
    frame = np.full((360, 640, 3), 200, dtype=np.uint8)
    built = screen_text.build_ocr_page_png(frame, region=(0.5, 1.0))
    assert built is not None
    png_bytes, out_w, out_h, top, bottom = built
    assert out_w == screen_text.READ_WIDTH
    assert out_h == 360
    assert (top, bottom) == (0.5, 1.0)
    decoded = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    assert decoded is not None
    assert decoded.shape == (out_h * 3, out_w)


@pytest.mark.asyncio
async def test_parallel_clip_rendering_preserves_order_and_runs_concurrently(tmp_path):
    service = build_task_service()
    service.config.clip_render_concurrency = 2
    service.config.temp_dir = str(tmp_path)
    service.task_repo.get_task_by_id = AsyncMock(return_value={"status": "queued"})
    service.task_repo.save_workflow_plan = AsyncMock()
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "video_path": str(tmp_path / "src.mp4"),
            "transcript": "hello world",
            "analysis_json": "{}",
            "segments": [],
            "segments_to_render": [
                {"start_time": "00:00", "end_time": "00:10", "text": "clip 1"},
                {"start_time": "00:10", "end_time": "00:20", "text": "clip 2"},
                {"start_time": "00:20", "end_time": "00:30", "text": "clip 3"},
            ],
        }
    )

    active = 0
    peak_active = 0
    created_filenames = []

    async def fake_create_single_clip(_video_path, seg, idx, out_dir, *_args, **_kwargs):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        # Clip 1 finishes slower than clip 2 to prove commit order stays deterministic.
        await asyncio.sleep(0.06 if idx == 0 else 0.01)
        active -= 1
        p = Path(out_dir) / f"clip_{idx + 1}.mp4"
        p.write_bytes(b"mp4")
        return {
            "clip_id": idx + 1,
            "filename": p.name,
            "path": str(p),
            "start_time": seg["start_time"],
            "end_time": seg["end_time"],
            "duration": 10.0,
            "text": seg["text"],
        }

    async def fake_create_clip(_db, **kwargs):
        created_filenames.append((kwargs["clip_order"], kwargs["filename"]))
        return f"clip-id-{kwargs['clip_order']}"

    service.video_service.create_single_clip = AsyncMock(side_effect=fake_create_single_clip)
    service.clip_repo.create_clip = AsyncMock(side_effect=fake_create_clip)

    await service.process_task(task_id="task-par", url="upload://src.mp4", source_type="upload")

    assert peak_active == 2
    assert created_filenames == [(1, "clip_1.mp4"), (2, "clip_2.mp4"), (3, "clip_3.mp4")]
