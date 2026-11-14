"""WP14: generation with dynamic edits, end to end (FINAL §6.1, §6.6).

Real renders (lavfi sources, real ffmpeg, the deno chain with the committed fx-core bundle) are skipped when ffmpeg
or deno is missing. The beat model is a pydantic-ai ``FunctionModel``; nothing touches the network.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src import video_utils
from src.clip_source_map import clip_source_map_path, load_clip_edit_fx
from src.edit_fx import beats_llm
from src.edit_fx import env as fx_env
from src.edit_fx.contracts import EditStyle, FxPlan
from src.edit_fx.jobs import FxJob
from src.edit_fx.warp import build_warp
from src.media import fx_audio, fx_chain
from src.media.fx_chain import FxRenderInput, FxResult
from src.services import video_service
from src.services.video_service import VideoService, plan_clip_fx
from tests.unit.test_task_service import build_clip_result, build_task_service

FPS = 30
SPEECH = (
    "So this is honestly the craziest thing that ever happened to me. We made one hundred thousand dollars "
    "in a single day. And then everything just fell apart in one hour."
)
HOOK = "The craziest day ever"


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


requires_media = pytest.mark.skipif(
    not (_have("ffmpeg") and _have("ffprobe") and _have(fx_env.fx_deno_bin())),
    reason="needs ffmpeg, ffprobe and deno",
)


# --------------------------------------------------------------------------- sources and probes


def make_source(path: Path, *, w: int, h: int, dur: float) -> Path:
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r={FPS}:d={dur}",
        "-f", "lavfi", "-i", f"sine=f=220:r=48000:d={dur}",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(path),
    ], check=True, capture_output=True)
    return path


def write_transcript(video: Path, *, start: float = 0.2, step: float = 0.3) -> list[dict]:
    """A transcript cache (ms) of SPEECH; returns the words on the source timeline in seconds."""
    words, t = [], start
    for token in SPEECH.split():
        words.append({"text": token, "start": int(round(t * 1000)), "end": int(round((t + 0.24) * 1000)),
                      "confidence": 0.99, "speaker": "A"})
        t += step
    video.with_suffix(".transcript_cache.json").write_text(json.dumps(
        {"version": 2, "words": words, "utterances": [], "text": SPEECH, "language_code": "en"}))
    return [{"id": f"w{i}", "text": w["text"], "start": w["start"] / 1000, "end": w["end"] / 1000}
            for i, w in enumerate(words)]


def streams(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-show_entries",
                          "stream=codec_type,width,height,duration,nb_read_packets", "-of", "json", str(path)],
                         check=True, capture_output=True, text=True).stdout
    found = {s["codec_type"]: s for s in json.loads(out)["streams"]}
    return found


def frame_at(path: Path, t: float, w: int, h: int) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3).astype(np.int16)


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    if not (_have("ffmpeg") and _have("ffprobe")):
        pytest.skip("needs ffmpeg")
    root = tmp_path_factory.mktemp("fx_generation")
    out = {}
    for name, (w, h, dur) in {"portrait": (720, 1280, 8.0), "small": (360, 640, 3.0),
                              "landscape": (1920, 1080, 2.4)}.items():
        directory = root / name
        directory.mkdir()
        video = make_source(directory / "source.mp4", w=w, h=h, dur=dur)
        out[name] = SimpleNamespace(video=video, words=write_transcript(video), w=w, h=h, dur=dur)
    return out


# --------------------------------------------------------------------------- the beat model


def tag(kind, first, last, strength=3, tone="hype", emoji="", label=""):
    return {"kind": kind, "first_word": first, "last_word": last, "strength": strength, "tone": tone,
            "emoji": emoji, "label": label}


@pytest.fixture
def beat_model(monkeypatch):
    """Route the beat agent to a FunctionModel (no network). ``calls`` counts model requests."""
    calls: list[str] = []

    async def function(messages, info: AgentInfo) -> ModelResponse:
        calls.append("call")
        out = {"mood": "hype", "sensitivity": "normal", "hook_last_word": 11, "reactions": [], "beats": [
            tag("hook", 0, 11), tag("number", 15, 18, emoji="💰"), tag("build", 19, 22, 2),
            tag("payoff", 23, 28, 3, "shocked", label="IT FELL APART"),
        ]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out)])

    config = SimpleNamespace(openai_api_key="", openrouter_api_key="", openrouter_fallback_model="x/y",
                             google_api_key="test-key", anthropic_api_key="", ollama_base_url="", ollama_api_key="",
                             as_runtime_settings=lambda: {}, resolve_ollama_base_url=lambda: "http://127.0.0.1:1/v1/")
    monkeypatch.setattr(beats_llm, "_beats_agent", None)
    monkeypatch.setattr(beats_llm, "_beats_agent_signature", None)
    monkeypatch.setattr(beats_llm, "apply_settings_to_process_env", lambda settings: None)
    monkeypatch.setattr(beats_llm, "get_config", lambda: config)
    monkeypatch.setattr(beats_llm, "get_posthog_client", lambda: None)
    monkeypatch.setattr(beats_llm, "_build_model", lambda _cfg, _name: FunctionModel(function))
    monkeypatch.setenv("EDIT_PLAN_LLM", "google-gla:gemini-test")
    monkeypatch.setattr(video_service, "_beats_redis", lambda: None)
    return calls


@pytest.fixture(autouse=True)
def quiet_analytics(monkeypatch):
    monkeypatch.setattr(video_utils, "_capture_fx_event", lambda *_a, **_k: None)
    for name in ("FX_RUNTIME", "FX_WARP", "FX_DENO_BIN", "FX_CHAIN_MODE", "FX_LOUDNORM"):
        monkeypatch.delenv(name, raising=False)


SEGMENT = {"start_time": "00:00", "end_time": "00:08", "text": SPEECH, "relevance_score": 0.9, "reasoning": "r",
           "hook_title": HOOK, "keep_ranges": [[0.1, 7.9]]}


# --------------------------------------------------------------------------- end to end


@requires_media
async def test_generation_end_to_end_with_a_fake_beat_model(sources, beat_model, monkeypatch, tmp_path):
    order: list[str] = []
    real_chain, real_cap = fx_chain.run_fx_chain, video_service.cap_video_short_side

    def chain(**kwargs):
        order.append("fx_chain")
        return real_chain(**kwargs)

    def cap(path, side):
        order.append("cap")
        return real_cap(path, side)

    monkeypatch.setattr(fx_chain, "run_fx_chain", chain)
    monkeypatch.setattr(video_service, "cap_video_short_side", cap)
    checkpoint: dict = {}

    async def on_beats(index, beats):
        checkpoint[str(index)] = beats

    out_dir = tmp_path / "clips"
    out_dir.mkdir()
    job = FxJob(style=EditStyle(intensity="max"), task_id="task-e2e", clip_index=0, on_beats=on_beats)
    info = await VideoService.create_single_clip(
        sources["portrait"].video, dict(SEGMENT), 0, out_dir, caption_template="marker_circle",
        max_short_side=720, fx=job,
    )
    assert info is not None
    assert info["fx_meta"] == {"fx_level": "full", "timing": "word", "intensity": "max",
                               "template": "marker_circle"}
    assert len(beat_model) == 1 and list(checkpoint) == ["0"]
    # The 720p cap runs after the FX render (§0.4.3) and keeps the audio.
    assert order == ["fx_chain", "cap"]
    probed = streams(Path(info["path"]))
    assert int(probed["video"]["width"]) == 720 and "audio" in probed
    payload = json.loads(clip_source_map_path(Path(info["path"])).read_text())
    assert {"edit_style", "edit_beats", "edit_fx", "edit_fx_warp", "fx_level", "vertical_layout"} <= set(payload)
    assert payload["fx_level"] == "full" and payload["edit_style"] == {"intensity": "max"}
    assert "zoompan" not in payload["vertical_layout"]["video_filter"]       # never Ken Burns on the FX path
    sidecar = load_clip_edit_fx(Path(info["path"]))
    plan = FxPlan.model_validate(sidecar["plan"])
    assert plan.seed == fx_chain_seed("task-e2e", 0)
    expected_warp = build_warp(plan.cues, plan.edit_duration).to_json()
    assert sidecar["applied_warp"] == expected_warp
    assert sidecar["beats"]["source"] == "llm" and sidecar["beats"]["timeline_hash"]
    # A/V agree within a frame, warped or not.
    assert abs(float(probed["video"]["duration"]) - float(probed["audio"]["duration"])) <= 0.034
    # A retry/resume reuses the checkpointed beats: no second model call.
    planned, _ = await plan_clip_fx(
        video_path=sources["portrait"].video, keep_ranges=[tuple(r) for r in info["keep_ranges"]], output_offsets=None,
        group_output_starts=[], style=job.style, template="marker_circle", task_id="task-e2e", clip_index=0,
        hook_title=HOOK, max_clip_seconds=60.0, cached_beats=checkpoint["0"],
    )
    assert planned.rule == "cached" and len(beat_model) == 1
    assert [b.id for b in planned.beats.beats] == [
        b["id"] for b in sidecar["beats"]["beats"]]


def fx_chain_seed(task_id: str, index: int) -> int:
    from src.edit_fx.rng import plan_seed

    return plan_seed(task_id, index)


@requires_media
async def test_style_only_vertical_render_gets_baseline_pushes_not_ken_burns(sources, monkeypatch, tmp_path):
    monkeypatch.setattr(video_service, "plan_beats", AsyncMock(side_effect=AssertionError("no model for style only")))
    out_dir = tmp_path / "clips"
    out_dir.mkdir()
    job = FxJob(style=EditStyle(), task_id="task-kb", clip_index=0)
    info = await VideoService.create_single_clip(
        sources["portrait"].video, dict(SEGMENT), 0, out_dir, caption_template="marker_circle", fx=job,
    )
    assert info is not None and info["fx_meta"]["fx_level"] == "full"
    sidecar = load_clip_edit_fx(Path(info["path"]))
    plan = FxPlan.model_validate(sidecar["plan"])
    pushes = [c for c in plan.cues if c.id.startswith("kb")]
    assert pushes and all(c.kind == "zoom" and c.mode == "push" for c in pushes)
    assert not [c for c in plan.cues if c.kind in ("sfx", "bleep")]          # marker_circle has no intrinsic SFX
    payload = json.loads(clip_source_map_path(Path(info["path"])).read_text())
    assert "zoompan" not in payload["vertical_layout"]["video_filter"]
    assert sidecar["style"] == {}


# --------------------------------------------------------------------------- planning (no render)


async def test_style_only_plans_without_the_model_and_intrinsic_sfx_follow_the_template(sources, monkeypatch):
    monkeypatch.setattr(video_service, "plan_beats", AsyncMock(side_effect=AssertionError("no model call")))
    common = dict(video_path=sources["portrait"].video, keep_ranges=[(0.1, 7.9)], output_offsets=None,
                  group_output_starts=[], style=EditStyle(), task_id="t", clip_index=0, hook_title=None,
                  max_clip_seconds=60.0)
    marker, _ = await plan_clip_fx(template="marker_circle", **common)
    slam, _ = await plan_clip_fx(template="slam", **common)
    assert marker.rule == slam.rule == "heuristic"
    assert not [c for c in marker.plan.cues if c.kind == "sfx"]
    assert [c for c in slam.plan.cues if c.kind == "sfx" and c.sound in ("impact", "boom")]
    assert not [c for c in slam.plan.cues if c.kind == "bleep"]                # D3: style only never bleeps


async def test_words_carry_source_times_and_approx_timing_comes_from_utterances(tmp_path):
    video = tmp_path / "v.mp4"
    # A provider with caption lines but no word timings (G12): approximate timing from the utterances.
    video.with_suffix(".transcript_cache.json").write_text(json.dumps({
        "version": 2, "words": [],
        "utterances": [{"text": "Hello there friend", "start": 10_000, "end": 11_500}],
    }))
    transcript = json.loads(video.with_suffix(".transcript_cache.json").read_text())
    assert video_service.fx_words_for_ranges(transcript, [(9.0, 12.0)]) == []
    lines = video_service.fx_lines_for_ranges(transcript, [(9.0, 12.0)])
    assert lines == [{"start": 1.0, "end": 2.5, "text": "Hello there friend", "src_start": 10.0, "src_end": 11.5}]
    planned, _ = await plan_clip_fx(video_path=video, keep_ranges=[(9.0, 12.0)], output_offsets=None,
                                    group_output_starts=[], style=EditStyle(), task_id="t", clip_index=0,
                                    hook_title=None, max_clip_seconds=60.0, template="marker_circle")
    assert planned.timing == "approx" and [w["text"] for w in planned.words] == ["Hello", "there", "friend"]
    # Approximate words carry A times too, so their beats can re-anchor on a later re-render.
    assert planned.words[0]["src_start"] == 10.0 and planned.words[-1]["src_end"] == 11.5
    transcript["words"] = [{"text": "Hi", "start": 9_500, "end": 9_800}]
    (word,) = video_service.fx_words_for_ranges(transcript, [(9.0, 12.0)])
    assert (word["start"], word["end"], word["src_start"], word["src_end"]) == (0.5, 0.8, 9.5, 9.8)


async def test_section_downloads_give_beats_original_source_times(tmp_path):
    """A partial YouTube download is shifted onto a local timeline; A times go back to the original video."""
    from src.youtube_source import TimeMap, TimeSpan, save_time_map

    video = tmp_path / "sections.mp4"
    save_time_map(video, TimeMap((TimeSpan(100.0, 120.0, 0.0),)))  # local 0-20 s is original 100-120 s
    write_transcript(video, start=2.0)
    planned, _ = await plan_clip_fx(video_path=video, keep_ranges=[(2.0, 6.0)], output_offsets=None,
                                    group_output_starts=[], style=EditStyle(), task_id="t", clip_index=0,
                                    hook_title=None, max_clip_seconds=60.0, template="marker_circle")
    first = planned.words[0]
    assert first["start"] == 0.0 and first["src_start"] == pytest.approx(102.0)
    assert all(b.src_start is None or b.src_start >= 100.0 for b in planned.beats.beats)


async def test_failed_fx_planning_renders_the_clip_without_effects(monkeypatch, tmp_path):
    """Decorative layers never cost the clip (§0.4.7): a planning error renders legacy, no fx kwarg."""
    from src.media.render_ranges import RenderRanges

    async def boom(**_kwargs):
        raise RuntimeError("resolver bug")

    seen: dict = {}

    def fake_render(*args, **kwargs):
        seen.update(kwargs)
        Path(args[3]).write_bytes(b"clip")
        return True

    monkeypatch.setattr(video_service, "plan_clip_fx", boom)
    monkeypatch.setattr(video_service, "create_optimized_clip", fake_render)
    monkeypatch.setattr(video_service, "resolve_render_ranges", lambda *_a: RenderRanges(
        keep_ranges=[(1.0, 5.0)], text="t", output_offsets=None, group_output_starts=[], bounds=(1.0, 5.0)))
    monkeypatch.setattr(video_service, "rendered_clip_duration", lambda *_a: 4.0)
    out = tmp_path / "clips"
    out.mkdir()
    job = FxJob(style=EditStyle(intensity="bold"), task_id="t", clip_index=0)
    info = await VideoService.create_single_clip(
        tmp_path / "v.mp4", {"start_time": "00:01", "end_time": "00:05", "text": "t"}, 0, out,
        caption_template="marker_circle", fx=job,
    )
    assert info is not None and "fx_meta" not in info and "fx" not in seen


# --------------------------------------------------------------------------- process_task (resume, checkpoint)


def _task_service(row: dict, segments: list[dict]):
    service = build_task_service()
    service.task_repo.get_task_by_id = AsyncMock(return_value=row)
    service.task_repo.save_workflow_plan = AsyncMock()
    service.video_service.process_video_complete = AsyncMock(return_value={
        "clips": [build_clip_result()], "segments_to_render": segments, "video_path": "/tmp/source.mp4",
        "segments": [], "summary": None, "key_topics": [], "transcript": "Transcript", "analysis_json": "{}",
    })
    return service


async def test_resume_of_an_fx_task_renders_fx_from_the_row():
    service = _task_service({"status": "queued", "edit_style": '{"intensity": "bold", "grain": true}'},
                            [{"start": 0, "end": 10}])
    service.video_service.create_single_clip = AsyncMock(return_value={**build_clip_result(), "fx_meta": {
        "fx_level": "full", "timing": "word", "intensity": "bold", "template": "hormozi"}})
    await service.process_task(task_id="task-9", url="upload://source.mp4", source_type="upload",
                               caption_template="hormozi", clip_length="medium")
    job = service.video_service.create_single_clip.await_args.kwargs["fx"]
    assert job.style == EditStyle(intensity="bold", grain=True)
    assert (job.task_id, job.clip_index, job.cached_beats) == ("task-9", 0, None)
    assert service.clip_repo.create_clip.await_args.kwargs["fx_meta"]["fx_level"] == "full"


async def test_style_only_task_renders_fx():
    service = _task_service({"status": "queued"}, [{"start": 0, "end": 10}])
    await service.process_task(task_id="t", url="upload://source.mp4", source_type="upload",
                               caption_template="marker_circle", clip_length="medium")
    job = service.video_service.create_single_clip.await_args.kwargs["fx"]
    assert not job.style.active
    assert "fx_meta" not in service.clip_repo.create_clip.await_args.kwargs


async def test_a_retried_clip_reuses_its_checkpointed_beats():
    service = _task_service({"status": "queued", "edit_style": {"intensity": "bold"}},
                            [{"start": 0, "end": 10, "required": True}])
    seen: list = []

    async def render(*_args, fx=None, **_kwargs):
        seen.append(fx.cached_beats)
        if len(seen) == 1:
            await fx.on_beats(fx.clip_index, {"beats": "first attempt"})
            return None
        return build_clip_result()

    service.video_service.create_single_clip = AsyncMock(side_effect=render)
    await service.process_task(task_id="t", url="upload://source.mp4", source_type="upload",
                               caption_template="default", clip_length="medium")
    assert seen == [None, {"beats": "first attempt"}]


async def test_two_clips_beats_both_survive_in_the_checkpoint():
    service = _task_service({"status": "queued", "edit_style": {"intensity": "subtle"}},
                            [{"start": 0, "end": 10}, {"start": 20, "end": 30}])

    async def render(*_args, fx=None, **_kwargs):
        await fx.on_beats(fx.clip_index, {"clip": fx.clip_index})
        return {**build_clip_result(), "filename": f"clip-{fx.clip_index}.mp4"}

    service.video_service.create_single_clip = AsyncMock(side_effect=render)
    await service.process_task(task_id="t", url="upload://source.mp4", source_type="upload",
                               caption_template="default", clip_length="medium")
    patches = [call.args[3] for call in service.checkpoint_repo.save.await_args_list if "edit_beats" in call.args[3]]
    assert patches[-1]["edit_beats"] == {"0": {"clip": 0}, "1": {"clip": 1}}


# --------------------------------------------------------------------------- direct renders (fast, small frames)


def small_plan(cues, *, duration=2.9, template="default", intensity="bold", **extra) -> FxPlan:
    return FxPlan(style=EditStyle(intensity=intensity), template=template, seed=5, edit_duration=duration,
                  cues=tuple(cues), **extra)


def render_small(source, out: Path, plan: FxPlan | None, *, hook=None, broll=None, report=None, fmt="original",
                 add_subtitles=False, template="default"):
    report = {} if report is None else report
    ok = video_utils.create_optimized_clip(
        source.video, 0.0, 2.9, out, add_subtitles, None, None, None, template, fmt, [(0.0, 2.9)], hook,
        extend_to_sentence=False, layout_report=report, broll_overlays=broll,
        **({"fx": FxRenderInput(plan=plan, words=[dict(w) for w in source.words])} if plan is not None else {}),
    )
    return ok, report


WARP_CUES = (
    {"kind": "freeze", "id": "b1.freeze", "start": 1.0, "end": 1.0, "hold": 0.6, "label": "WAIT"},
    {"kind": "speed", "id": "b2.fast", "start": 2.0, "end": 2.6, "rate": 2.0},
)


@requires_media
def test_warped_render_keeps_audio_and_video_within_a_frame(sources, tmp_path):
    ok, report = render_small(sources["small"], tmp_path / "warp.mp4", small_plan(WARP_CUES))
    assert ok and report["fx_level"] == "full"
    assert report["applied_warp"] == build_warp(small_plan(WARP_CUES).cues, 2.9).to_json() != []
    probed = streams(tmp_path / "warp.mp4")
    video, audio = float(probed["video"]["duration"]), float(probed["audio"]["duration"])
    assert abs(video - audio) <= 0.034
    assert video == pytest.approx(2.9 + 0.6 - 0.3, abs=0.04)


@requires_media
def test_av_mismatch_retries_with_an_identity_warp(sources, tmp_path, monkeypatch):
    checks: list[bool] = []

    def mismatch(path):
        checks.append(True)
        return len(checks) == 1

    monkeypatch.setattr(video_utils, "_av_mismatch", mismatch)
    ok, report = render_small(sources["small"], tmp_path / "identity.mp4", small_plan(WARP_CUES))
    assert ok and report["fx_level"] == "full" and report["applied_warp"] == []
    assert float(streams(tmp_path / "identity.mp4")["video"]["duration"]) == pytest.approx(2.9, abs=0.04)


@requires_media
def test_fx_pixels_are_drawn_inside_the_cue_window(sources, tmp_path):
    source = sources["small"]
    flash = {"kind": "flash", "id": "b1.flash", "start": 1.0, "end": 1.4, "peak": 0.9}
    ok, report = render_small(source, tmp_path / "fx.mp4", small_plan([flash]))
    assert ok and report["fx_level"] == "full"
    assert render_small(source, tmp_path / "legacy.mp4", None)[0]
    inside = np.abs(frame_at(tmp_path / "fx.mp4", 1.1, 360, 640) - frame_at(tmp_path / "legacy.mp4", 1.1, 360, 640))
    outside = np.abs(frame_at(tmp_path / "fx.mp4", 2.2, 360, 640) - frame_at(tmp_path / "legacy.mp4", 2.2, 360, 640))
    assert inside.mean() > 20
    assert outside.mean() < 4


@requires_media
def test_without_deno_the_clip_renders_safe_with_audio_fx_and_the_hook_title(sources, tmp_path, monkeypatch):
    monkeypatch.setenv("FX_DENO_BIN", "false")
    buses: list[bool] = []
    real_bus = fx_audio.build_bus
    ass_calls: list[dict] = []
    real_ass = video_utils.build_assemblyai_ass_subtitles

    def bus(*args, **kwargs):
        buses.append(real_bus(*args, **kwargs))
        return buses[-1]

    def ass(*args, **kwargs):
        ass_calls.append(kwargs)
        return real_ass(*args, **kwargs)

    monkeypatch.setattr(fx_audio, "build_bus", bus)
    monkeypatch.setattr(video_utils, "build_assemblyai_ass_subtitles", ass)
    plan = small_plan([{"kind": "sfx", "id": "b0.sfx", "start": 0.5, "end": 0.5, "sound": "impact"},
                       {"kind": "accent", "id": "b0.slam", "start": 0.2, "end": 1.2, "variant": "slam"}],
                      template="slam", hook_title_suppressed=True)
    ok, report = render_small(sources["small"], tmp_path / "safe.mp4", plan, hook=HOOK, template="slam",
                              add_subtitles=True)
    assert ok and report["fx_level"] == "safe" and report["applied_warp"] == []
    assert buses and buses[-1] is True                                     # SFX mixed at level safe
    assert ass_calls[-1]["hook_title"] == HOOK and ass_calls[-1]["include_captions"] is True
    assert "audio" in streams(tmp_path / "safe.mp4")


@requires_media
def test_failing_safe_falls_back_to_the_legacy_render_with_ken_burns(sources, tmp_path, monkeypatch):
    def failing_chain(**kwargs):
        return FxResult(ok=False, width=kwargs["out_w"], height=kwargs["out_h"], level=kwargs["mode"],
                        out_duration=0.0, face_track=None, applied_warp=[], timings={}, error="p3: exit 1")

    renders: list[dict] = []
    real_render = video_utils.render_reframed_clip_ffmpeg

    def spy(*args, **kwargs):
        renders.append({k: v for k, v in kwargs.items() if k not in ("subtitle_ass_path",)})
        return real_render(*args, **kwargs)

    monkeypatch.setattr(fx_chain, "run_fx_chain", failing_chain)
    monkeypatch.setattr(video_utils, "render_reframed_clip_ffmpeg", spy)
    source = sources["portrait"]
    report: dict = {}
    ok = video_utils.create_optimized_clip(
        source.video, 0.1, 7.9, tmp_path / "none.mp4", True, None, None, None, "marker_circle", "vertical",
        [(0.1, 7.9)], HOOK, extend_to_sentence=False, layout_report=report,
        fx=FxRenderInput(plan=small_plan([], duration=7.8, template="marker_circle"), words=list(source.words)),
    )
    assert ok and report["fx_level"] == "none"
    legacy_render = renders[-1]
    assert "fx_request" not in legacy_render
    assert "zoompan" in legacy_render["vertical_plan"][0]
    assert "zoompan" in report["vertical_layout"]["video_filter"]
    # The same render the legacy pipeline makes for this clip (its ASS fallback template).
    legacy_report: dict = {}
    assert video_utils.create_optimized_clip(
        source.video, 0.1, 7.9, tmp_path / "legacy.mp4", True, None, None, None, "default", "vertical",
        [(0.1, 7.9)], HOOK, extend_to_sentence=False, layout_report=legacy_report,
    )
    assert renders[-1] == legacy_render


@requires_media
def test_a_broken_broll_file_renders_full_without_broll(sources, tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video at all")
    ok, report = render_small(sources["small"], tmp_path / "out.mp4",
                              small_plan([{"kind": "flash", "id": "b1.flash", "start": 1.0, "end": 1.3}]),
                              broll=[{"path": str(broken), "start": 0.5, "duration": 1.2, "offset": 0.0}])
    assert ok and report["fx_level"] == "full"
    assert report["broll_applied"] is False


@requires_media
def test_landscape_original_render_keeps_drawn_fx_inside_the_frame(sources, tmp_path):
    source = sources["landscape"]
    plan = small_plan([{"kind": "accent", "id": "b1.accent", "start": 0.5, "end": 1.6, "variant": "underline",
                        "text": "craziest"}], duration=2.3, template="marker_circle", intensity="off")
    report: dict = {}
    ok = video_utils.create_optimized_clip(
        source.video, 0.0, 2.3, tmp_path / "fx.mp4", True, None, None, None, "marker_circle", "original",
        [(0.0, 2.3)], None, extend_to_sentence=False, layout_report=report,
        fx=FxRenderInput(plan=plan, words=list(source.words)),
    )
    assert ok and report["fx_level"] == "full"
    probed = streams(tmp_path / "fx.mp4")
    assert (int(probed["video"]["width"]), int(probed["video"]["height"])) == (1920, 1080)
    assert video_utils.create_optimized_clip(
        source.video, 0.0, 2.3, tmp_path / "legacy.mp4", False, None, None, None, "default", "original",
        [(0.0, 2.3)], None, extend_to_sentence=False,
    )
    diff = np.abs(frame_at(tmp_path / "fx.mp4", 1.0, 1920, 1080) - frame_at(tmp_path / "legacy.mp4", 1.0, 1920, 1080))
    drawn = diff.max(axis=2) > 60
    # Drop isolated pixels: yuv420 chroma rounding differs between the fx chain and the legacy encode at sharp colour
    # edges of the moving test pattern (a few stray pixels, also at the frame edge). Drawn strokes survive this.
    core = (drawn[1:-1, 1:-1] & drawn[:-2, 1:-1] & drawn[2:, 1:-1] & drawn[1:-1, :-2] & drawn[1:-1, 2:])
    ys, xs = np.nonzero(core)
    ys, xs = ys + 1, xs + 1
    assert len(xs) > 500                                                   # captions are drawn
    # Inside the frame with a margin: nothing drawn touches the frame edges (u-scaled, aspect-aware layout).
    assert xs.min() > 20 and xs.max() < 1920 - 20
    assert ys.min() > 20 and ys.max() < 1080 - 20
