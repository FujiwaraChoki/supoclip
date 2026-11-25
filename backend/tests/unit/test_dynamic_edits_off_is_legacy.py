"""WP14 invariant: dynamic edits off + a legacy template is the legacy pipeline (FINAL §0.4.1).

For each of the 7 legacy templates, with the style off (absent, ``{}``, ``{"intensity": "off"}`` or the JSON string
asyncpg may return), a spy over the whole generation path asserts: no new kwarg on any call, no deno process, no
FX sidecar key, no beat-model call, no resolver call and no ``fx_meta``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import video_utils
from src.caption_templates import CAPTION_TEMPLATES
from src.clip_source_map import FX_SIDECAR_KEYS, clip_source_map_path
from src.edit_fx.contracts import LEGACY_CAPTION_TEMPLATES
from src.media import fx_chain
from src.media.render_ranges import RenderRanges
from src.services import video_service
from src.services.video_service import VideoService
from tests.unit.test_task_service import build_task_service

OFF_STYLES = [None, {}, {"intensity": "off"}, '{"intensity": "off"}', {"intensity": "off", "music": False}]
NEW_RENDER_KWARGS = {"fx", "caption_override", "fx_request", "fx_report", "allow_kenburns", "time_map",
                     "censor_spans", "fx_enabled", "fx_jobs"}


def test_the_seven_legacy_templates():
    assert tuple(CAPTION_TEMPLATES) == LEGACY_CAPTION_TEMPLATES


@pytest.fixture(autouse=True)
def no_fx_side_effects(monkeypatch):
    """Anything dynamic-edits-only fails the test when touched."""

    def forbidden(name):
        def fail(*_args, **_kwargs):
            raise AssertionError(f"{name} must not run when dynamic edits are off")
        return fail

    monkeypatch.setattr(fx_chain.subprocess, "Popen", forbidden("deno/fx chain Popen"))
    monkeypatch.setattr(fx_chain, "run_fx_chain", forbidden("run_fx_chain"))
    monkeypatch.setattr(video_service, "plan_beats", forbidden("plan_beats"))
    monkeypatch.setattr("src.edit_fx.beats_llm.plan_beats", forbidden("beats_llm.plan_beats"))
    monkeypatch.setattr(video_service, "resolve", forbidden("resolve"))
    monkeypatch.setattr(video_service, "plan_clip_fx", forbidden("plan_clip_fx"))


@pytest.mark.parametrize("template", LEGACY_CAPTION_TEMPLATES)
@pytest.mark.parametrize("style", OFF_STYLES, ids=["absent", "empty", "off", "json_string", "off_with_keys"])
async def test_process_task_passes_no_fx_job(template, style):
    service = build_task_service()
    row = {"status": "queued"}
    if style is not None:
        row["edit_style"] = style
    service.task_repo.get_task_by_id = AsyncMock(return_value=row)
    service.task_repo.save_workflow_plan = AsyncMock()

    await service.process_task(task_id="task-1", url="upload://source.mp4", source_type="upload",
                               caption_template=template, clip_length="medium")

    render = service.video_service.create_single_clip.await_args
    assert "fx" not in render.kwargs
    assert "fx_meta" not in service.clip_repo.create_clip.await_args.kwargs
    saved = [call.args[3] for call in service.checkpoint_repo.save.await_args_list]
    assert not any("edit_beats" in patch for patch in saved)


@pytest.fixture
def render_spy(monkeypatch, tmp_path):
    """The real ``create_single_clip`` → ``create_optimized_clip`` with the ffmpeg/analysis leaves recorded."""
    calls: dict[str, list] = {}

    def record(name, value=None):
        def fn(*args, **kwargs):
            calls.setdefault(name, []).append(kwargs)
            return value(*args, **kwargs) if callable(value) else value
        return fn

    def source_render(_video, _ranges, out, **_kwargs):
        Path(out).write_bytes(b"source")
        return True

    def ass(*args, **_kwargs):
        Path(args[5]).write_text("[Script Info]\n")
        return True

    def final(_source, final_path, *_args, **_kwargs):
        Path(final_path).write_bytes(b"clip")
        return True, 1080, 1920

    def plan(_path, _w, _h, _turns, _words, report=None, **_kwargs):
        if report is not None:
            report["kind"] = "crop"
        return "crop=608:1080:656:0,zoompan=z=1", "vf", 0.72

    monkeypatch.setattr(video_utils, "get_config", lambda: SimpleNamespace(speaker_framing=False))
    monkeypatch.setattr(video_utils, "render_source_ranges_ffmpeg", record("render_source_ranges_ffmpeg", source_render))
    monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda _p: (1920, 1080))
    monkeypatch.setattr(video_utils, "ffprobe_duration", lambda _p: 9.0)
    monkeypatch.setattr(video_utils, "probe_video_geometry",
                        lambda _p: SimpleNamespace(display_width=1920, display_height=1080, fps=30.0))
    monkeypatch.setattr(video_utils, "build_vertical_filter_plan", record("build_vertical_filter_plan", plan))
    monkeypatch.setattr(video_utils, "_resolve_source_captions", lambda *_a, **_k: (None, "none"))
    monkeypatch.setattr(video_utils, "build_assemblyai_ass_subtitles", record("build_assemblyai_ass_subtitles", ass))
    monkeypatch.setattr(video_utils, "render_reframed_clip_ffmpeg", record("render_reframed_clip_ffmpeg", final))
    monkeypatch.setattr(video_service, "resolve_render_ranges", lambda *_a: RenderRanges(
        keep_ranges=[(10.0, 19.0)], text="text", output_offsets=None, group_output_starts=[], bounds=(10.0, 19.0)))
    monkeypatch.setattr(video_service, "cap_video_short_side", lambda *_a: True)
    monkeypatch.setattr(video_service, "rendered_clip_duration", lambda *_a: 9.0)
    return SimpleNamespace(calls=calls, out=tmp_path / "clips", video=tmp_path / "source.mp4")


@pytest.mark.parametrize("template", LEGACY_CAPTION_TEMPLATES)
@pytest.mark.parametrize("output_format", ["vertical", "original"])
async def test_create_single_clip_without_fx_is_untouched(render_spy, template, output_format):
    render_spy.out.mkdir(exist_ok=True)
    segment = {"start_time": "00:10", "end_time": "00:19", "text": "text", "relevance_score": 0.9,
               "reasoning": "r", "hook_title": "A headline"}
    info = await VideoService.create_single_clip(
        render_spy.video, segment, 0, render_spy.out, caption_template=template, output_format=output_format,
        max_short_side=720,
    )
    assert info is not None and "fx_meta" not in info
    for name, kwargs_list in render_spy.calls.items():
        for kwargs in kwargs_list:
            assert not set(kwargs) & NEW_RENDER_KWARGS, (name, sorted(kwargs))
    # Legacy ASS: the template's own captions and the hook title.
    ass = render_spy.calls["build_assemblyai_ass_subtitles"][0]
    assert ass["include_captions"] is True and ass["hook_title"] == "A headline"
    assert "template_override" not in ass and "caption_words" not in ass
    payload = json.loads(clip_source_map_path(Path(info["path"])).read_text())
    assert not set(payload) & set(FX_SIDECAR_KEYS)
    # vertical_layout is written by create_single_clip only on the FX path (the batch path always wrote it).
    assert "vertical_layout" not in payload


async def test_create_video_clips_without_fx_jobs(monkeypatch, tmp_path):
    seen: dict = {}

    def fake_batch(*args, **kwargs):
        seen["kwargs"] = kwargs
        return []

    monkeypatch.setattr(video_service, "create_clips_with_transitions", fake_batch)
    monkeypatch.setattr(video_service, "get_config", lambda: SimpleNamespace(temp_dir=str(tmp_path)))
    for template in LEGACY_CAPTION_TEMPLATES:
        await VideoService.create_video_clips(tmp_path / "v.mp4", [{"start_time": "00:00", "end_time": "00:05"}],
                                              caption_template=template)
        assert set(seen["kwargs"]) == {"max_clip_seconds"}
