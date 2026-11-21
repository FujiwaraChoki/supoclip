"""WP18 §13.1: settings re-render of dynamic-edits clips, end to end through ``regenerate_all_clips_for_task``.

The stored FX keys of each clip's sidecar ride on its segment into ``create_video_clips``, whose async planner applies
the rules in order (FX → legacy, reuse, re-resolve, re-anchor, beat model, heuristic). The beat model and the
resolver are counted; the batch renderer is replaced by one that writes the sidecar a real FX render writes, so
re-renders chain like they do in production.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.clip_cleanup import normalize_clip_cleanup_settings
from src.clip_source_map import (
    clip_source_map_path,
    load_clip_edit_fx,
    save_clip_edit_fx,
    save_clip_source_ranges,
)
from src.edit_fx import beats as fx_beats
from src.edit_fx.contracts import FxPlan
from src.media.ffmpeg import output_offsets_for_ranges
from src.media.render_ranges import RenderRanges
from src.services import clip_service as clip_service_module
from src.services import video_service
from src.services.task_service import TaskService

SENTENCE = (
    "This is honestly the fucking craziest thing. We made one hundred thousand dollars in a single day. "
    "Nobody believed us at all. And then the whole thing fell apart in one hour."
)
SOURCE_RANGE = (10.0, 23.0)
# Cleanup that removes "honestly" (10.6-10.84 A): every later word moves 0.3 s earlier on the clip timeline.
CLEANED_RANGES = [(10.0, 10.6), (10.9, 23.0)]
SWEAR_A_START = 11.2            # "fucking" (word 4, 0.3 s per word from 10.0)


def write_transcript(video: Path) -> None:
    words, t = [], 10_000
    for token in SENTENCE.split():
        words.append({"text": token, "start": t, "end": t + 240, "confidence": 0.99, "speaker": "A"})
        t += 300
    video.with_suffix(".transcript_cache.json").write_text(json.dumps(
        {"version": 2, "words": words, "utterances": [], "text": SENTENCE, "language_code": "en"}))


@pytest.fixture
def env(monkeypatch, tmp_path, isolated_clip_edits):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    write_transcript(video)
    (tmp_path / "clips").mkdir()
    calls: dict = {"llm": 0, "resolve": 0, "batches": []}
    real_resolve = video_service.resolve

    async def fake_plan_beats(words, clip_seconds, *, timing, language=None, redis=None, emoji_keys=None):
        calls["llm"] += 1
        return fx_beats.heuristic_beats(words, timing=timing).model_copy(update={"source": "llm", "model": "t:m"})

    def counting_resolve(*args, **kwargs):
        calls["resolve"] += 1
        return real_resolve(*args, **kwargs)

    def fake_ranges(_video, segment, _cleanup, _max):
        if segment.get("source_ranges"):
            keep = list(CLEANED_RANGES)          # recomputed cleanup
        else:
            keep = [tuple(r) for r in segment["keep_ranges"]]
        return RenderRanges(keep_ranges=keep, text=segment.get("text", ""), output_offsets=None,
                            group_output_starts=[], bounds=(keep[0][0], keep[-1][1]))

    def fake_batch(video_path, segments, output_dir, *args, **kwargs):
        calls["batches"].append({"segments": segments, "kwargs": kwargs})
        infos = []
        for index, segment in enumerate(segments):
            path = tmp_path / "clips" / f"clip_{uuid4().hex[:8]}.mp4"
            path.write_bytes(b"clip")
            save_clip_source_ranges(path, fake_ranges(None, segment, None, None).keep_ranges)
            info = {"clip_id": index + 1, "filename": path.name, "path": str(path), "start_time": "00:10",
                    "end_time": "00:23", "duration": 13.0, "text": SENTENCE}
            if kwargs.get("fx_enabled") and segment.get("edit_fx"):
                plan = segment["edit_fx"]
                save_clip_edit_fx(path, style=segment["edit_style"], beats=segment["edit_beats"], plan=plan,
                                  face_track=None, fx_level="full", applied_warp=[])
                info["fx_meta"] = {"fx_level": "full", "timing": plan["timing"],
                                   "intensity": plan["style"]["intensity"], "template": plan["template"]}
            infos.append(info)
        return infos

    async def no_overlays(_placements):
        return []

    monkeypatch.setattr(video_service, "plan_beats", fake_plan_beats)
    monkeypatch.setattr(video_service, "resolve", counting_resolve)
    monkeypatch.setattr(video_service, "resolve_render_ranges", fake_ranges)
    monkeypatch.setattr(video_service, "create_clips_with_transitions", fake_batch)
    monkeypatch.setattr(video_service, "_beats_redis", lambda: None)
    monkeypatch.setattr(video_service, "get_config", lambda: SimpleNamespace(temp_dir=str(tmp_path)))
    monkeypatch.setattr(video_service, "broll_overlays_for", no_overlays)
    monkeypatch.setattr(clip_service_module, "resolve_upload_for_edit", AsyncMock(return_value=video))

    first_clip = tmp_path / "clips" / "generated.mp4"
    first_clip.write_bytes(b"clip")
    save_clip_source_ranges(first_clip, [SOURCE_RANGE])
    state = SimpleNamespace(
        task={"id": "task-1", "source_url": "upload://source.mp4", "source_type": "video_url", "user_id": None,
              "include_broll": False, "edit_style": None},
        clips=[_row(first_clip)],
        created=[],
    )

    service = TaskService(db=None)
    service.task_repo.get_task_by_id = AsyncMock(side_effect=lambda *_: dict(state.task))
    service.task_repo.update_task_clips = AsyncMock()
    service.clip_repo.get_clips_by_task = AsyncMock(side_effect=lambda *_: list(state.clips))
    service.clip_repo.delete_clips_by_task = AsyncMock()

    async def create_clip(_db, **kwargs):
        state.created.append(kwargs)
        return f"clip-{len(state.created)}"

    service.clip_repo.create_clip = create_clip
    service.video_service.prepare_source = AsyncMock(side_effect=lambda path: path)
    monkeypatch.setattr(service, "_media_limits", AsyncMock(return_value=(None, None)))

    async def settings(_task_id):
        return {"output_format": "vertical", "add_subtitles": True, "clip_length": "auto",
                **normalize_clip_cleanup_settings()}

    monkeypatch.setattr(service, "_load_task_source_settings", settings)

    async def run(*, style=None, template="marker_circle", cleanup=None):
        state.task["edit_style"] = style
        calls.update(llm=0, resolve=0)
        state.created.clear()
        await service.regenerate_all_clips_for_task(
            "task-1", None, 34, "#FFFFFF", template,
            **({"cleanup_settings": cleanup} if cleanup is not None else {}),
        )
        state.clips = [_row(Path(kwargs["file_path"]), kwargs.get("fx_meta")) for kwargs in state.created]
        return calls["batches"][-1]

    return SimpleNamespace(run=run, calls=calls, state=state, service=service, tmp=tmp_path)


def _row(path: Path, fx_meta=None) -> dict:
    return {
        "id": f"row-{path.stem}", "task_id": "task-1", "file_path": str(path), "start_time": "00:10",
        "end_time": "00:23", "duration": 13.0, "text": SENTENCE, "relevance_score": 0.9, "reasoning": "x",
        "virality_score": 80, "hook_score": 20, "engagement_score": 20, "value_score": 20,
        "shareability_score": 20, "hook_type": "story", "hook_title": None, "clip_order": 1,
        "workflow_role": None, "part_number": None, "workflow_meta": None,
        **({"fx_meta": fx_meta} if fx_meta else {}),
    }


def _plan(row: dict) -> FxPlan:
    sidecar = load_clip_edit_fx(Path(row["file_path"]))
    assert sidecar is not None and sidecar["plan"] is not None
    return FxPlan.model_validate(sidecar["plan"])


def _bleeps(plan: FxPlan) -> list[float]:
    return [round(cue.start, 3) for cue in plan.cues if cue.kind == "bleep"]


BOLD = {"intensity": "bold"}


async def test_off_to_bold_without_beats_makes_one_model_call(env):
    batch = await env.run(style=BOLD)
    assert env.calls["llm"] == 1 and env.calls["resolve"] == 1
    assert batch["kwargs"]["fx_enabled"] is True
    (created,) = env.state.created
    assert created["fx_meta"] == {"fx_level": "full", "timing": "word", "intensity": "bold", "template": "marker_circle"}
    plan = _plan(env.state.clips[0])
    assert plan.style.intensity == "bold" and plan.template == "marker_circle"
    # The swear is bleeped at its spoken time on the clip timeline (A 11.2 → S 1.2, 20 ms lead).
    assert _bleeps(plan) == [pytest.approx(SWEAR_A_START - SOURCE_RANGE[0] - 0.02, abs=1e-6)]


async def test_unchanged_timeline_and_style_reuse_the_plan_without_resolver_or_model(env):
    await env.run(style=BOLD)
    stored = _plan(env.state.clips[0])
    await env.run(style=BOLD)
    assert env.calls["llm"] == 0 and env.calls["resolve"] == 0
    assert _plan(env.state.clips[0]) == stored


async def test_style_change_re_resolves_without_the_model(env):
    await env.run(style=BOLD)
    seed = _plan(env.state.clips[0]).seed
    await env.run(style={"intensity": "max"})
    assert env.calls["llm"] == 0 and env.calls["resolve"] == 1
    plan = _plan(env.state.clips[0])
    assert plan.style.intensity == "max" and plan.seed == seed


async def test_cleanup_change_reanchors_and_bleeps_the_new_swear_time_without_the_model(env):
    await env.run(style=BOLD)
    assert _bleeps(_plan(env.state.clips[0])) == [pytest.approx(1.18, abs=1e-6)]
    batch = await env.run(style=BOLD, cleanup=normalize_clip_cleanup_settings(False, None, True, ["honestly"]))
    assert env.calls["llm"] == 0 and env.calls["resolve"] == 1
    # The segment was re-cut from source with the planner told not to reuse the stored plan.
    (segment,) = batch["segments"]
    assert "fx_cleanup_recomputed" not in segment
    plan = _plan(env.state.clips[0])
    # "fucking" now starts (11.2 - 10.9) s into the second kept range, which starts where the renderer's junction
    # crossfade puts it: the bleep follows the word (20 ms lead).
    second_offset = output_offsets_for_ranges(CLEANED_RANGES)[1]
    assert _bleeps(plan) == [pytest.approx(round(second_offset + SWEAR_A_START - 10.9 - 0.02, 3), abs=2e-3)]
    assert _bleeps(plan) != [pytest.approx(1.18, abs=1e-3)]
    beats = load_clip_edit_fx(Path(env.state.clips[0]["file_path"]))["beats"]
    swear = [beat for beat in beats["beats"] if beat["kind"] == "swear"]
    assert swear and swear[0]["src_start"] == pytest.approx(SWEAR_A_START)


async def test_low_beat_survival_plans_with_the_model_again(env):
    await env.run(style=BOLD)
    path = clip_source_map_path(Path(env.state.clips[0]["file_path"]))
    payload = json.loads(path.read_text())
    for beat in payload["edit_beats"]["beats"]:
        beat["src_start"] = (beat.get("src_start") or 0) + 500.0
        beat["src_end"] = (beat.get("src_end") or 0) + 500.0
    path.write_text(json.dumps(payload))
    await env.run(style=BOLD, cleanup=normalize_clip_cleanup_settings(False, None, True, ["honestly"]))
    assert env.calls["llm"] == 1


async def test_fx_to_legacy_never_resolves_and_drops_the_fx_keys(env):
    await env.run(style=BOLD)
    batch = await env.run(style=None, template="hormozi")
    assert env.calls["llm"] == 0 and env.calls["resolve"] == 0
    assert batch["kwargs"]["fx_enabled"] is True        # the batch clears FX keys from the new sidecar
    (segment,) = batch["segments"]
    for key in ("edit_fx", "edit_beats", "edit_style", "face_track", "edit_fx_warp", "fx_words"):
        assert key not in segment
    (created,) = env.state.created
    assert "fx_meta" not in created
    assert load_clip_edit_fx(Path(created["file_path"])) is None


async def test_a_corrupt_sidecar_is_planned_again_and_never_deleted(env):
    await env.run(style=BOLD)
    path = clip_source_map_path(Path(env.state.clips[0]["file_path"]))
    payload = json.loads(path.read_text())
    payload["edit_fx"] = {"version": 1, "cues": "not a list"}
    payload["edit_beats"] = "garbage"
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    await env.run(style=BOLD)
    assert env.calls["llm"] == 1 and env.calls["resolve"] == 1
    assert path.read_bytes() == before
    assert "edit_fx" in json.loads(path.read_text())


async def test_legacy_clips_with_a_legacy_target_get_the_legacy_call(env, monkeypatch):
    captured = {}

    async def create_video_clips(*args, **kwargs):
        captured.update(kwargs)
        return [{"clip_id": 1, "filename": "n.mp4", "path": str(env.tmp / "n.mp4"), "start_time": "00:10",
                 "end_time": "00:23", "duration": 13.0, "text": SENTENCE}]

    monkeypatch.setattr(env.service.video_service, "create_video_clips", create_video_clips)
    await env.service.regenerate_all_clips_for_task("task-1", None, 34, "#FFFFFF", "hormozi")
    assert "fx_jobs" not in captured


async def test_style_only_template_without_intensity_uses_heuristic_beats(env):
    await env.run(style=None, template="marker_circle")
    assert env.calls["llm"] == 0 and env.calls["resolve"] == 1
    plan = _plan(env.state.clips[0])
    assert plan.style.intensity == "off" and plan.template == "marker_circle"
    assert _bleeps(plan) == []                             # D3: style only changes no audio


# --------------------------------------------------------------------------- section downloads (WP14 residual)


def _section_transcript(local_offset: float) -> dict:
    """The SENTENCE transcript as a section download sees it: original A 10.0 s lands at ``local_offset``."""
    words, t = [], 10_000
    for token in SENTENCE.split():
        start = t - 10_000 + round(local_offset * 1000)
        words.append({"text": token, "start": start, "end": start + 240, "confidence": 0.99, "speaker": "A"})
        t += 300
    return {"version": 2, "words": words, "utterances": [], "text": SENTENCE, "language_code": "en"}


def _section_map(local_offset: float, *, split_at: float | None = None):
    from src.youtube_source import TimeMap, TimeSpan

    if split_at is None:
        return TimeMap((TimeSpan(original_start=8.0, original_end=26.0, local_start=local_offset - 2.0),))
    # The same stretch stitched from two sections that touch at ``split_at`` (original time).
    first = TimeSpan(original_start=8.0, original_end=split_at, local_start=local_offset - 2.0)
    return TimeMap((first, TimeSpan(original_start=split_at, original_end=26.0, local_start=first.local_end)))


def test_section_download_timeline_hash_is_in_original_time():
    words = [{"text": "a", "start": 0.0, "end": 0.2}]
    one = video_service._fx_timeline_hash([(2.0, 15.0)], None, words, _section_map(2.0))
    other = video_service._fx_timeline_hash([(30.0, 43.0)], None, words, _section_map(30.0, split_at=17.5))
    assert one == other == fx_beats.timeline_hash([(10.0, 23.0)], None, words)
    # Without a section map the clip's ranges are already source time: the hash is what it always was.
    assert video_service._fx_timeline_hash([(10.0, 23.0)], None, words, None) == fx_beats.timeline_hash(
        [(10.0, 23.0)], None, words)
    assert one != video_service._fx_timeline_hash([(2.0, 15.5)], None, words, _section_map(2.0))


async def test_a_new_section_download_of_the_same_clip_reuses_its_plan(monkeypatch, tmp_path):
    from src.edit_fx.contracts import EditStyle

    layout = {"offset": 2.0, "split": None}
    monkeypatch.setattr(video_service, "load_cached_transcript_data",
                        lambda _path: _section_transcript(layout["offset"]))
    monkeypatch.setattr(video_service, "load_time_map",
                        lambda _path: _section_map(layout["offset"], split_at=layout["split"]))
    calls = {"llm": 0}

    async def fake_plan_beats(words, clip_seconds, *, timing, language=None, redis=None, emoji_keys=None):
        calls["llm"] += 1
        return fx_beats.heuristic_beats(words, timing=timing).model_copy(update={"source": "llm", "model": "t:m"})

    monkeypatch.setattr(video_service, "plan_beats", fake_plan_beats)
    monkeypatch.setattr(video_service, "_beats_redis", lambda: None)
    style = EditStyle(intensity="bold")

    async def plan(stored=None):
        local = layout["offset"]
        planned, _ = await video_service.plan_clip_fx(
            video_path=tmp_path / "section.mp4", keep_ranges=[(local, local + 13.0)], output_offsets=None,
            group_output_starts=None, style=style, template="marker_circle", task_id="task-1", clip_index=0,
            hook_title=None, max_clip_seconds=None, stored=stored,
        )
        return planned

    first = await plan()
    assert first.rule == "llm" and calls["llm"] == 1
    stored = {"edit_fx": first.plan.model_dump(mode="json"), "edit_beats": first.beats.model_dump(mode="json"),
              "edit_style": style.to_json(), "face_track": None}
    # Re-render from another download: the clip sits elsewhere in the file and straddles two stitched sections.
    layout.update(offset=31.0, split=17.5)
    again = await plan(stored)
    assert again.rule == "reuse" and calls["llm"] == 1
    assert again.plan == first.plan
    again_max = await video_service.plan_clip_fx(
        video_path=tmp_path / "section.mp4", keep_ranges=[(31.0, 44.0)], output_offsets=None,
        group_output_starts=None, style=EditStyle(intensity="max"), template="marker_circle", task_id="task-1",
        clip_index=0, hook_title=None, max_clip_seconds=None, stored=stored,
    )
    assert again_max[0].rule == "re-resolve" and calls["llm"] == 1
