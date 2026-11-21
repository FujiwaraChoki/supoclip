"""WP14: settings re-render planning is hoisted into async ``create_video_clips`` (§6.1.3, §13.1).

``create_video_clips(..., fx_jobs=...)`` decides every segment's effects in async code (reuse / re-resolve /
re-anchor / beat model / heuristic) and stores the ready plan on the segment; the synchronous batch renderer
(``create_clips_from_segments``) only consumes ``segment["edit_fx"]`` and never calls the resolver or the model.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import video_utils
from src.clip_source_map import clip_source_map_path, load_clip_edit_fx, save_clip_source_ranges
from src.edit_fx import beats as fx_beats
from src.edit_fx import resolver as fx_resolver
from src.edit_fx.contracts import EditStyle, FxPlan
from src.edit_fx.jobs import FxJob
from src.media.render_ranges import RenderRanges
from src.services import video_service
from src.services.video_service import VideoService

SENTENCE = (
    "This is honestly the craziest thing. We made one hundred thousand dollars in a single day. "
    "Nobody believed us at all. And then the whole thing fell apart in one hour."
)


def write_transcript(video: Path, *, start_ms: int = 10_000, step_ms: int = 300) -> list[dict]:
    words = []
    t = start_ms
    for token in SENTENCE.split():
        words.append({"text": token, "start": t, "end": t + 240, "confidence": 0.99, "speaker": "A"})
        t += step_ms
    video.with_suffix(".transcript_cache.json").write_text(json.dumps(
        {"version": 2, "words": words, "utterances": [], "text": SENTENCE, "language_code": "en"}))
    return words


@pytest.fixture
def batch(monkeypatch, tmp_path):
    """create_video_clips with the renderer captured; the beat model and the resolver counted."""
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    write_transcript(video)
    calls: dict = {"llm": 0, "resolve": 0, "batch": None}
    real_resolve = video_service.resolve

    async def fake_plan_beats(words, clip_seconds, *, timing, language=None, redis=None, emoji_keys=None):
        calls["llm"] += 1
        heuristic = fx_beats.heuristic_beats(words, timing=timing)
        return heuristic.model_copy(update={"source": "llm", "model": "test:model"})

    def counting_resolve(*args, **kwargs):
        calls["resolve"] += 1
        return real_resolve(*args, **kwargs)

    def fake_ranges(_video, segment, _cleanup, _max):
        keep = [tuple(r) for r in segment["keep_ranges"]]
        return RenderRanges(keep_ranges=keep, text=segment.get("text", ""), output_offsets=None,
                            group_output_starts=[], bounds=(keep[0][0], keep[-1][1]))

    def fake_batch(video_path, segments, output_dir, *args, **kwargs):
        calls["batch"] = {"segments": segments, "args": args, "kwargs": kwargs}
        return []

    monkeypatch.setattr(video_service, "plan_beats", fake_plan_beats)
    monkeypatch.setattr(video_service, "resolve", counting_resolve)
    monkeypatch.setattr(video_service, "resolve_render_ranges", fake_ranges)
    monkeypatch.setattr(video_service, "create_clips_with_transitions", fake_batch)
    monkeypatch.setattr(video_service, "_beats_redis", lambda: None)
    monkeypatch.setattr(video_service, "get_config", lambda: SimpleNamespace(temp_dir=str(tmp_path)))

    async def no_overlays(placements):
        return []

    monkeypatch.setattr(video_service, "broll_overlays_for", no_overlays)

    async def run(segments, *, template="marker_circle", style=EditStyle(intensity="bold"), jobs=True,
                  include_broll=False):
        calls.update(llm=0, resolve=0, batch=None)
        fx_jobs = {i: FxJob(style=style, task_id="task-1", clip_index=i) for i in range(len(segments))} if jobs else None
        await VideoService.create_video_clips(
            video, segments, caption_template=template, include_broll=include_broll,
            **({"fx_jobs": fx_jobs} if fx_jobs else {}),
        )
        return calls["batch"]

    return SimpleNamespace(run=run, calls=calls, video=video, tmp=tmp_path)


def segment(ranges=((10.0, 16.0), (16.5, 23.0)), **extra) -> dict:
    return {"start_time": "00:10", "end_time": "00:23", "text": SENTENCE, "relevance_score": 0.9,
            "reasoning": "x", "keep_ranges": [list(r) for r in ranges], **extra}


def stored_from(planned: dict) -> dict:
    """What WP18 loads from the re-rendered clip's sidecar into the segment."""
    return {key: planned[key] for key in ("edit_fx", "edit_beats", "edit_style")} | {
        "face_track": {"version": 1, "space": "picture", "fps": 6, "samples": [[0.0, [[0.4, 0.2, 0.2, 0.2, 0.9]]]]}
    }


async def test_planning_runs_in_the_async_function_and_the_batch_only_consumes(batch):
    out = await batch.run([segment()])
    assert batch.calls["llm"] == 1 and batch.calls["resolve"] == 1
    assert out["kwargs"]["fx_enabled"] is True
    (planned,) = out["segments"]
    plan = FxPlan.model_validate(planned["edit_fx"])
    assert plan.template == "marker_circle" and plan.style.intensity == "bold"
    assert planned["edit_beats"]["timeline_hash"]
    assert planned["edit_style"] == {"intensity": "bold"}
    assert planned["fx_words"] and all("src_start" in w for w in planned["fx_words"])
    assert planned["fx_reaction_files"] == {}


async def test_without_fx_jobs_the_batch_gets_no_fx_kwarg(batch):
    out = await batch.run([segment()], jobs=False)
    assert "fx_enabled" not in out["kwargs"]
    assert "edit_fx" not in out["segments"][0]
    assert batch.calls["llm"] == 0 and batch.calls["resolve"] == 0


async def test_rule_1_off_and_legacy_never_resolves(batch):
    first = (await batch.run([segment()]))["segments"][0]
    out = await batch.run([segment(**stored_from(first))], template="hormozi", style=EditStyle())
    (seg,) = out["segments"]
    assert batch.calls["resolve"] == 0 and batch.calls["llm"] == 0
    for key in ("edit_fx", "edit_beats", "edit_style", "face_track", "fx_words"):
        assert key not in seg


async def test_rule_2_reuses_the_stored_plan_verbatim(batch):
    first = (await batch.run([segment()]))["segments"][0]
    out = await batch.run([segment(**stored_from(first))])
    (seg,) = out["segments"]
    assert batch.calls["resolve"] == 0 and batch.calls["llm"] == 0
    assert seg["edit_fx"] == first["edit_fx"]
    assert seg["face_track"]["samples"]                          # reused on the same timeline


async def test_rule_3_another_template_re_resolves_with_the_stored_seed(batch):
    first = (await batch.run([segment()]))["segments"][0]
    out = await batch.run([segment(**stored_from(first))], template="slam")
    (seg,) = out["segments"]
    assert batch.calls["resolve"] == 1 and batch.calls["llm"] == 0
    plan = FxPlan.model_validate(seg["edit_fx"])
    assert plan.template == "slam"
    assert plan.seed == FxPlan.model_validate(first["edit_fx"]).seed
    assert seg["face_track"]["samples"]


async def test_rule_3_another_style_re_resolves_without_the_model(batch):
    first = (await batch.run([segment()]))["segments"][0]
    out = await batch.run([segment(**stored_from(first))], style=EditStyle(intensity="max"))
    assert batch.calls["resolve"] == 1 and batch.calls["llm"] == 0
    assert FxPlan.model_validate(out["segments"][0]["edit_fx"]).style.intensity == "max"


async def test_rule_4_a_moved_timeline_reanchors_beats_by_source_time(batch):
    first = (await batch.run([segment()]))["segments"][0]
    # Cleanup now cuts 0.6 s more out of the junction: the words move on the clip timeline.
    moved = segment(ranges=((10.0, 15.4), (16.5, 23.0)), **stored_from(first))
    out = await batch.run([moved])
    (seg,) = out["segments"]
    assert batch.calls["llm"] == 0 and batch.calls["resolve"] == 1
    assert seg["edit_beats"]["timeline_hash"] != first["edit_beats"]["timeline_hash"]
    old_ids = {b["id"] for b in first["edit_beats"]["beats"]}
    new_ids = {b["id"] for b in seg["edit_beats"]["beats"]}
    assert new_ids & old_ids                                       # beats keep their ids (stable cue seeds)
    assert "face_track" not in seg                                # recomputed on a new timeline


async def test_cleanup_recompute_skips_reuse(batch):
    first = (await batch.run([segment()]))["segments"][0]
    out = await batch.run([segment(fx_cleanup_recomputed=True, **stored_from(first))])
    assert batch.calls["resolve"] == 1 and batch.calls["llm"] == 0
    assert "fx_cleanup_recomputed" not in out["segments"][0]


async def test_rule_5_without_stored_beats_plans_with_the_model(batch):
    stored = {"edit_fx": None, "edit_beats": None, "edit_style": None}
    await batch.run([segment(**stored)])
    assert batch.calls["llm"] == 1


async def test_a_corrupt_sidecar_is_treated_as_no_stored_data(batch):
    corrupt = {"edit_fx": {"cues": "nope"}, "edit_beats": {"beats": 7}, "edit_style": "?"}
    out = await batch.run([segment(**corrupt)])
    assert batch.calls["llm"] == 1
    assert FxPlan.model_validate(out["segments"][0]["edit_fx"]).cues is not None


async def test_rule_6_style_only_uses_heuristic_beats(batch):
    out = await batch.run([segment()], template="marker_circle", style=EditStyle())
    assert batch.calls["llm"] == 0 and batch.calls["resolve"] == 1
    assert out["segments"][0]["edit_beats"]["source"] == "heuristic"
    assert out["segments"][0]["edit_style"] == {}


async def test_stored_broll_windows_reach_the_resolver(batch, monkeypatch):
    seen = {}
    real = video_service.resolve

    def spy(inp, *args, **kwargs):
        seen["broll"] = inp.broll_windows
        return real(inp, *args, **kwargs)

    monkeypatch.setattr(video_service, "resolve", spy)
    footage = batch.tmp / "stock.mp4"
    footage.write_bytes(b"x")
    seg = segment(broll=[{"start": 2.0, "duration": 1.5, "path": str(footage)}])
    await batch.run([seg], include_broll=True)
    assert seen["broll"] == ((2.0, 3.5),)


# --------------------------------------------------------------------------- the synchronous batch renderer


WARP = [
    {"e0": 0.0, "e1": 3.0, "o0": 0.0, "rate": 1.0, "hold": 0.0},
    {"e0": 3.0, "e1": 3.0, "o0": 3.0, "rate": 0.0, "hold": 0.6},
    {"e0": 3.0, "e1": 13.0, "o0": 3.6, "rate": 1.0, "hold": 0.0},
]


@pytest.fixture
def renderer(monkeypatch, tmp_path):
    calls: list[dict] = []

    def fake_create(video, start, end, output, *args, **kwargs):
        calls.append(kwargs)
        Path(output).write_bytes(b"clip")
        report = kwargs["layout_report"]
        report["broll_applied"] = False
        if kwargs.get("fx") is not None:
            report.update(fx_level="full", applied_warp=WARP,
                          face_track=None, fx_plan=kwargs["fx"].plan,
                          vertical_layout={"kind": "crop", "video_filter": "crop", "mode": "vf",
                                           "width": 1920, "height": 1080, "duration": 7.5})
        return True

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the batch renderer must not plan")

    def install():
        monkeypatch.setattr(video_utils, "create_optimized_clip", fake_create)
        monkeypatch.setattr(video_utils, "rendered_clip_duration", lambda *_a: 7.5)
        monkeypatch.setattr(video_utils, "resolve_render_ranges", lambda _v, seg, _c, _m: RenderRanges(
            keep_ranges=[tuple(r) for r in seg["keep_ranges"]], text=seg["text"], output_offsets=None,
            group_output_starts=[], bounds=(10.0, 23.0)))
        monkeypatch.setattr(fx_resolver, "resolve", forbidden)
        monkeypatch.setattr(video_service, "resolve", forbidden)
        monkeypatch.setattr(video_service, "plan_beats", forbidden)
        monkeypatch.setattr("src.edit_fx.beats_llm.plan_beats", forbidden)

    return SimpleNamespace(calls=calls, out=tmp_path / "clips", install=install)


async def test_batch_renderer_consumes_edit_fx_and_records_the_sidecar(batch, renderer):
    planned = (await batch.run([segment()]))["segments"][0]
    renderer.install()
    stale = renderer.out / "stale"   # a legacy segment in the same batch: its new sidecar has no FX keys
    legacy = segment()
    clips = video_utils.create_clips_from_segments(
        batch.video, [planned, legacy], renderer.out, None, None, None, "marker_circle", fx_enabled=True,
    )
    fx_call, legacy_call = renderer.calls
    assert fx_call["fx"].plan == FxPlan.model_validate(planned["edit_fx"])
    assert fx_call["fx"].words == planned["fx_words"]
    assert "fx" not in legacy_call
    fx_clip, legacy_clip = clips
    assert fx_clip["fx_meta"] == {"fx_level": "full", "timing": "word", "intensity": "bold",
                                  "template": "marker_circle"}
    assert "fx_meta" not in legacy_clip
    sidecar = load_clip_edit_fx(Path(fx_clip["path"]))
    assert sidecar["fx_level"] == "full" and sidecar["applied_warp"] == WARP
    assert sidecar["style"] == {"intensity": "bold"}
    assert json.loads(clip_source_map_path(Path(fx_clip["path"])).read_text())["vertical_layout"]["kind"] == "crop"
    assert load_clip_edit_fx(Path(legacy_clip["path"])) is None
    assert not stale.exists()


def test_batch_renderer_without_fx_enabled_ignores_edit_fx(renderer, tmp_path):
    renderer.install()
    seg = segment(edit_fx={"anything": True})
    video = tmp_path / "v.mp4"
    clips = video_utils.create_clips_from_segments(video, [seg], renderer.out, None, None, None, "marker_circle")
    assert "fx" not in renderer.calls[0] and "fx_meta" not in clips[0]


def test_legacy_rerender_clears_fx_keys_from_the_new_sidecar(renderer, tmp_path, monkeypatch):
    # save_clip_source_ranges could carry an old sidecar's keys forward; the legacy re-render clears them.
    from src import clip_source_map

    def save_with_stale(clip_path, ranges, offsets):
        save_clip_source_ranges(clip_path, ranges, offsets)
        path = clip_source_map_path(clip_path)
        payload = json.loads(path.read_text())
        payload.update(edit_fx={"stale": True}, fx_level="full")
        path.write_text(json.dumps(payload))

    renderer.install()
    monkeypatch.setattr(video_utils, "save_clip_source_ranges", save_with_stale)
    clips = video_utils.create_clips_from_segments(tmp_path / "v.mp4", [segment()], renderer.out, None, None, None,
                                                   "hormozi", fx_enabled=True)
    payload = json.loads(clip_source_map_path(Path(clips[0]["path"])).read_text())
    assert not set(payload) & set(clip_source_map.FX_SIDECAR_KEYS)


# --------------------------------------------------------------------------- fx_meta in PostgreSQL (needs TEST_DATABASE_URL)


async def test_clip_repository_writes_and_reads_fx_meta_only_when_set(db_session, tmp_path):
    from src.repositories.clip_repository import ClipRepository
    from tests.fixtures.factories import create_source, create_task, create_user

    user = await create_user(db_session)
    user_id = user["id"] if isinstance(user, dict) else str(user)
    source = await create_source(db_session)
    source_id = source["id"] if isinstance(source, dict) else str(source)
    task = await create_task(db_session, user_id=user_id, source_id=source_id)
    task_id = task["id"] if isinstance(task, dict) else str(task)
    meta = {"fx_level": "full", "timing": "word", "intensity": "bold", "template": "marker_circle"}

    def clip_args(order: int) -> dict:
        path = tmp_path / f"clip_{order}.mp4"
        path.write_bytes(b"x")
        return dict(task_id=task_id, filename=path.name, file_path=str(path), start_time="00:00",
                    end_time="00:05", duration=5.0, text="t", relevance_score=0.9, reasoning="r", clip_order=order)

    fx_id = await ClipRepository.create_clip(db_session, **clip_args(1), fx_meta=meta)
    legacy_id = await ClipRepository.create_clip(db_session, **clip_args(2))
    clips = {c["id"]: c for c in await ClipRepository.get_clips_by_task(db_session, task_id)}
    assert clips[fx_id]["fx_meta"] == meta
    assert "fx_meta" not in clips[legacy_id]

    edited = {**meta, "fx_level": "safe"}
    await ClipRepository.update_clip(db_session, fx_id, filename="clip_1.mp4", file_path=clip_args(1)["file_path"],
                                     start_time="00:00", end_time="00:04", duration=4.0, text="t", fx_meta=edited)
    await ClipRepository.update_clip(db_session, legacy_id, filename="clip_2.mp4",
                                     file_path=clip_args(2)["file_path"], start_time="00:00", end_time="00:04", duration=4.0, text="t",
                                     workflow_meta={"part": 1}, fx_meta=meta)
    clips = {c["id"]: c for c in await ClipRepository.get_clips_by_task(db_session, task_id)}
    assert clips[fx_id]["fx_meta"] == edited
    assert clips[legacy_id]["fx_meta"] == meta and clips[legacy_id]["workflow_meta"] == {"part": 1}
