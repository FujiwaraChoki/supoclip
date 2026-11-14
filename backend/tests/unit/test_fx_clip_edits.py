"""WP18 §13.2-13.3: trim, split, merge and caption edits of dynamic-edits clips.

The frame tests cut real mp4s whose every frame encodes its edit time (E) as a grey level, through the applied
warp of a freeze, and check that the sidecar written for the cut maps E back onto the right frames: what fx-core
draws at E t (captions, accents) lands on the picture of E t.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.clip_source_map import (
    clip_source_map_path,
    load_clip_broll,
    load_clip_caption_settings,
    load_clip_edit_fx,
    load_clip_output_offsets,
    load_clip_source_ranges,
    save_clip_broll,
    save_clip_caption_settings,
    save_clip_edit_fx,
    save_clip_source_ranges,
)
from src.config import Config
from src.edit_fx.contracts import EditStyle, FxPlan
from src.edit_fx.warp import build_warp, warp_from_json
from src.media.render_ranges import rendered_duration
from src.services import clip_service as clip_service_module
from src.services import video_service
from src.services.task_service import TaskService

np = pytest.importorskip("numpy")
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg")

FPS = 30
W, H = 64, 112
GREY_PER_SECOND = 60.0


# ----------------------------------------------------------------------------- fixtures


def _plan(edit_duration: float, cues: list[dict], **globals_) -> FxPlan:
    return FxPlan.model_validate({
        "style": EditStyle(intensity="bold").model_dump(),
        "template": "marker_circle",
        "seed": 7,
        "edit_duration": edit_duration,
        "timing": "word",
        "cues": cues,
        **globals_,
    })


FREEZE = {"kind": "freeze", "id": "b1.freeze", "start": 1.0, "end": 1.0, "hold": 0.5, "beat": "b1"}
ACCENT = {"kind": "accent", "id": "b2.accent", "start": 2.0, "end": 2.4, "variant": "circle", "text": "two", "beat": "b2"}
SFX = {"kind": "sfx", "id": "b2.sfx", "start": 2.5, "end": 2.5, "sound": "whoosh", "beat": "b2"}


def write_frames(path: Path, greys: list[int]) -> None:
    """An mp4 at 30 fps whose frame n is a flat grey ``greys[n]`` (lossless, so levels survive one re-encode)."""
    frames = np.stack([np.full((H, W), grey, dtype=np.uint8) for grey in greys])
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "-", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{len(greys) / FPS:.4f}",
         "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        input=frames.tobytes(), check=True,
    )


def grey_at(path: Path, f: float) -> float:
    """Mean grey of the frame showing at final time ``f`` (every frame decoded, so no seek rounding)."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True,
    ).stdout
    frames = np.frombuffer(out, dtype=np.uint8).reshape(-1, H, W)
    return float(frames[min(len(frames) - 1, int(f * FPS + 1e-6))].mean())


def warped_clip(path: Path, *, plan: FxPlan, offset_grey: float = 0.0, scale: float = GREY_PER_SECOND,
                ranges=((20.0, 23.0),)) -> None:
    """An FX clip: frames through the plan's warp (a freeze repeats its frame), its sidecar as a render writes it."""
    warp = build_warp(plan.cues, plan.edit_duration)
    frames = round(warp.out_duration * FPS)
    write_frames(path, [round(offset_grey + warp.to_edit(n / FPS) * scale) for n in range(frames)])
    save_clip_source_ranges(path, list(ranges))
    save_clip_edit_fx(path, style={"intensity": "bold"}, beats=None, plan=plan.model_dump(mode="json"),
                      face_track={"version": 1, "space": "picture", "fps": 6,
                                  "samples": [[t / 6, [[0.4, 0.2, 0.2, 0.2, 0.9]]] for t in range(19)]},
                      fx_level="full", applied_warp=warp.to_json())


@pytest.fixture
def edits(tmp_path, isolated_clip_edits):
    config = Config()
    config.temp_dir = str(tmp_path)
    service = TaskService(db=None, config=config)
    rows: dict[str, dict] = {}
    updates: list[dict] = []
    created: list[dict] = []

    async def get_clip(_db, clip_id):
        return dict(rows[clip_id]) if clip_id in rows else None

    async def update_clip(_db, clip_id, filename, file_path, start, end, duration, text, **kwargs):
        updates.append({"clip_id": clip_id, "file_path": file_path, "start": start, "end": end,
                        "duration": duration, "text": text, **kwargs})
        rows[clip_id].update(file_path=file_path, duration=duration,
                             **({"fx_meta": kwargs["fx_meta"]} if kwargs.get("fx_meta") else {}))

    async def create_clip(_db, **kwargs):
        created.append(kwargs)
        return "new"

    service.clip_repo.get_clip_by_id = get_clip
    service.clip_repo.update_clip = update_clip
    service.clip_repo.create_clip = create_clip
    service.clip_repo.make_room_for_clip = AsyncMock()
    service.clip_repo.reorder_task_clips = AsyncMock()
    service.clip_repo.delete_clip = AsyncMock()

    def add(clip_id: str, path: Path, order: int = 1, fx: bool = True, **extra) -> None:
        rows[clip_id] = {
            "id": clip_id, "task_id": "task-1", "file_path": str(path), "start_time": "00:20", "end_time": "00:23",
            "duration": 3.5, "text": "caption", "clip_order": order, "workflow_meta": None, "hook_title": None,
            **({"fx_meta": {"fx_level": "full", "timing": "word", "intensity": "bold",
                            "template": "marker_circle"}} if fx else {}),
            **extra,
        }

    return SimpleNamespace(service=service, add=add, updates=updates, created=created, tmp=tmp_path, rows=rows)


def sidecar(path: str | Path) -> dict:
    return json.loads(clip_source_map_path(Path(path)).read_text())


# ----------------------------------------------------------------------------- trim


@needs_ffmpeg
async def test_trim_of_a_warped_clip_maps_f_offsets_through_the_applied_warp(edits):
    clip = edits.tmp / "fx.mp4"
    plan = _plan(3.0, [FREEZE, ACCENT, SFX])
    warped_clip(clip, plan=plan)
    edits.add("c1", clip)
    save_clip_broll(clip, [{"start": 0.5, "duration": 2.0, "video_id": 1}])
    save_clip_caption_settings(clip, {"position_y": 0.7, "position_is_center": True})

    # F 1.2 is inside the freeze (F 1.0-1.5 holds E 1.0); F 3.0 is E 2.5.
    await edits.service.trim_clip("task-1", "c1", 1.2, 0.5)

    (update,) = edits.updates
    out = Path(update["file_path"])
    fx = load_clip_edit_fx(out)
    new_plan = FxPlan.model_validate(fx["plan"])
    warp = warp_from_json(fx["applied_warp"], new_plan.edit_duration)
    assert new_plan.edit_duration == pytest.approx(1.5)
    # The cut through the hold keeps its remainder as a freeze at the new start.
    (freeze,) = [c for c in new_plan.cues if c.kind == "freeze"]
    assert freeze.start == 0 and freeze.hold == pytest.approx(0.3)
    (accent,) = [c for c in new_plan.cues if c.kind == "accent"]
    assert (accent.start, accent.end) == (pytest.approx(1.0), pytest.approx(1.4))
    assert not [c for c in new_plan.cues if c.kind == "sfx"]                 # E 2.5 is the cut end
    assert warp.out_duration == pytest.approx(1.8) and warp.pieces[0].hold == pytest.approx(0.3)
    # Source ranges are sliced in E (not "total - end_offset" in F).
    assert load_clip_source_ranges(out) == [(pytest.approx(21.0), pytest.approx(22.5))]
    # B-roll (E 0.5-2.5) carried in E: it now starts at the cut, 0.5 s into its footage.
    assert load_clip_broll(out) == [{"start": 0.0, "duration": 1.5, "video_id": 1, "footage_offset": 0.5}]
    assert load_clip_caption_settings(out) == {"position_y": 0.7, "position_is_center": True}
    assert fx["fx_level"] == "full" and fx["style"] == {"intensity": "bold"}
    # The face track follows the cut in E (old E 1.0 is new 0; one sample period of margin at most).
    assert -1 / 6 - 1e-3 <= fx["face_track"]["samples"][0][0] <= 0.0
    assert update["fx_meta"]["intensity"] == "bold"
    assert update["duration"] == pytest.approx(1.8, abs=0.05)
    # Frame test: what fx-core draws at new E t sits on the picture of old E (t + 1.0).
    for t in (0.0, 0.4, 1.0, 1.3):
        assert grey_at(out, warp.to_output(t)) == pytest.approx((t + 1.0) * GREY_PER_SECOND, abs=3)
    assert grey_at(out, 0.1) == pytest.approx(60, abs=3)                      # still frozen


async def test_a_progress_bar_clip_is_re_rendered_from_source_and_the_bar_spans_it(edits, monkeypatch):
    clip = edits.tmp / "bar.mp4"
    clip.write_bytes(b"clip")
    plan = _plan(10.0, [{**ACCENT, "start": 6.0, "end": 6.5}], progress_bar=True)
    save_clip_source_ranges(clip, [(20.0, 25.0), (26.0, 31.0)])
    save_clip_edit_fx(clip, style={"intensity": "bold", "progress_bar": True}, beats=None,
                      plan=plan.model_dump(mode="json"), face_track=None, fx_level="full", applied_warp=[])
    edits.add("c1", clip)
    render = _fake_render(edits, monkeypatch)
    monkeypatch.setattr(clip_service_module, "ffprobe_duration", lambda _p: 10.0)
    monkeypatch.setattr(clip_service_module, "trim_clip_file", _never("trim_clip_file"))

    await edits.service.trim_clip("task-1", "c1", 2.0, 1.0)

    (call,) = render.calls
    rendered_plan = call["fx"].plan
    assert rendered_plan.progress_bar is True
    keep, offsets = call["keep_ranges"], call["output_offsets"]
    # The bar fills over the plan's length, which is exactly the re-rendered timeline: it reaches 100 % at the end.
    assert rendered_plan.edit_duration == pytest.approx(rendered_duration(keep, offsets))
    assert rendered_plan.edit_duration == pytest.approx(7.0)
    assert [c.start for c in rendered_plan.cues if c.kind == "accent"] == [pytest.approx(4.0)]
    (update,) = edits.updates
    assert update["fx_meta"]["fx_level"] == "full"
    assert load_clip_edit_fx(Path(update["file_path"]))["plan"]["progress_bar"] is True


# ----------------------------------------------------------------------------- split


@needs_ffmpeg
@pytest.mark.parametrize("split_at", [2.0, 1.3], ids=["after_the_freeze", "through_the_freeze"])
async def test_split_of_a_warped_clip_keeps_both_halves_aligned(edits, split_at):
    clip = edits.tmp / "fx.mp4"
    warped_clip(clip, plan=_plan(3.0, [FREEZE, ACCENT]))
    edits.add("c1", clip)

    await edits.service.split_clip("task-1", "c1", split_at)

    (first_update,) = edits.updates
    (second,) = edits.created
    halves = [Path(first_update["file_path"]), Path(second["file_path"])]
    cut_e = 1.5 if split_at == 2.0 else 1.0
    e_starts = [0.0, cut_e]
    holds = []
    for path, e_start in zip(halves, e_starts):
        fx = load_clip_edit_fx(path)
        plan = FxPlan.model_validate(fx["plan"])
        warp = warp_from_json(fx["applied_warp"], plan.edit_duration)
        holds += [c.hold for c in plan.cues if c.kind == "freeze"]
        for share in (0.1, 0.5, 0.9):
            t = plan.edit_duration * share
            assert grey_at(path, warp.to_output(t)) == pytest.approx((t + e_start) * GREY_PER_SECOND, abs=3)
    # The hold is never lost or doubled by the cut: 0.5 s in total, split between the halves when cut through.
    assert sum(holds) == pytest.approx(0.5)
    assert len(holds) == (1 if split_at == 2.0 else 2)
    assert first_update["fx_meta"] and second["fx_meta"]
    assert load_clip_source_ranges(halves[1])[0][0] == pytest.approx(20.0 + cut_e)


# ----------------------------------------------------------------------------- merge


@needs_ffmpeg
async def test_merge_uses_edit_time_cursors_so_part_two_stays_aligned_after_a_freeze(edits):
    first, second = edits.tmp / "a.mp4", edits.tmp / "b.mp4"
    warped_clip(first, plan=_plan(3.0, [FREEZE]))
    warped_clip(second, plan=_plan(2.0, [{**ACCENT, "start": 0.5, "end": 0.9}]), offset_grey=190, scale=30,
                ranges=((40.0, 42.0),))
    save_clip_broll(second, [{"start": 0.2, "duration": 1.6, "video_id": 2}])
    edits.add("a", first, order=1)
    edits.add("b", second, order=2)

    result = await edits.service.merge_clips("task-1", ["b", "a"])

    assert result["clip_id"] == "a"
    (update,) = edits.updates
    merged = Path(update["file_path"])
    fx = load_clip_edit_fx(merged)
    plan = FxPlan.model_validate(fx["plan"])
    warp = warp_from_json(fx["applied_warp"], plan.edit_duration)
    ranges = load_clip_source_ranges(merged)
    # Cursor = part 1's E length (3.0), not its F length (3.5).
    assert load_clip_output_offsets(merged, ranges) == [0.0, pytest.approx(3.0)]
    assert plan.edit_duration == pytest.approx(5.0) and warp.out_duration == pytest.approx(5.5)
    assert [c.start for c in plan.cues if c.kind == "accent"] == [pytest.approx(3.5)]
    assert [c.id for c in plan.cues] == ["m0:b1.freeze", "m1:b2.accent"]
    assert load_clip_broll(merged) == [{"start": 3.2, "duration": 1.6, "video_id": 2}]
    assert update["duration"] == pytest.approx(5.5, abs=0.05)                # the probed F length
    for t in (3.2, 4.0, 4.8):
        assert grey_at(merged, warp.to_output(t)) == pytest.approx(190 + (t - 3.0) * 30, abs=3)
    for t in (0.5, 1.0, 2.0):
        assert grey_at(merged, warp.to_output(t)) == pytest.approx(t * GREY_PER_SECOND, abs=3)
    assert fx["face_track"]["samples"][-1][0] == pytest.approx(3.0 + 2.0 - 1 / 6, abs=0.2)


@needs_ffmpeg
async def test_merge_with_a_legacy_part_counts_it_with_e_equal_to_f(edits):
    first, second = edits.tmp / "a.mp4", edits.tmp / "legacy.mp4"
    warped_clip(first, plan=_plan(3.0, [FREEZE]))
    write_frames(second, [200] * 60)
    save_clip_source_ranges(second, [(40.0, 42.0)])
    edits.add("a", first, order=1)
    edits.add("l", second, order=2, fx=False)

    await edits.service.merge_clips("task-1", ["a", "l"])

    (update,) = edits.updates
    merged = Path(update["file_path"])
    fx = load_clip_edit_fx(merged)
    plan = FxPlan.model_validate(fx["plan"])
    assert plan.edit_duration == pytest.approx(5.0, abs=0.05)
    assert fx["face_track"] is None                       # the legacy part has none: recomputed later
    assert update["fx_meta"]["template"] == "marker_circle"
    ranges = load_clip_source_ranges(merged)
    assert load_clip_output_offsets(merged, ranges) == [0.0, pytest.approx(3.0)]
    assert update["duration"] == pytest.approx(5.5, abs=0.05)


async def test_merged_music_clip_is_re_rendered_from_source(edits, monkeypatch):
    music = {"mood": "hype", "bed": "hype"}
    paths = []
    for name in ("a", "b"):
        path = edits.tmp / f"{name}.mp4"
        path.write_bytes(b"clip")
        save_clip_source_ranges(path, [(10.0, 14.0)] if name == "a" else [(30.0, 33.0)])
        plan = _plan(4.0 if name == "a" else 3.0, [], music=music)
        save_clip_edit_fx(path, style={"intensity": "bold"}, beats=None, plan=plan.model_dump(mode="json"),
                          face_track=None, fx_level="full", applied_warp=[])
        edits.add(name, path, order=len(paths) + 1)
        paths.append(path)
    render = _fake_render(edits, monkeypatch)
    monkeypatch.setattr(clip_service_module, "ffprobe_duration", lambda p: 4.0 if Path(p).stem == "a" else 3.0)
    monkeypatch.setattr(clip_service_module, "merge_clip_files", _never("merge_clip_files"))

    await edits.service.merge_clips("task-1", ["a", "b"])

    (call,) = render.calls
    assert call["fx"].plan.music is not None and call["fx"].plan.edit_duration == pytest.approx(7.0)
    assert call["output_offsets"] == [0.0, pytest.approx(4.0)]


# ----------------------------------------------------------------------------- captions


def _write_transcript(video: Path) -> None:
    words, t = [], 20_000
    for token in "we made one hundred thousand dollars in a single massive day".split():
        words.append({"text": token, "start": t, "end": t + 240, "confidence": 0.99, "speaker": "A"})
        t += 300
    video.with_suffix(".transcript_cache.json").write_text(json.dumps(
        {"version": 2, "words": words, "utterances": [], "text": "", "language_code": "en"}))


class _Render:
    def __init__(self):
        self.calls: list[dict] = []


def _fake_render(edits, monkeypatch) -> _Render:
    """create_optimized_clip as the FX path reports it, recording its arguments; the source is an upload."""
    source = edits.tmp / "source.mp4"
    source.write_bytes(b"source")
    _write_transcript(source)
    render = _Render()

    def create_optimized_clip(video_path, start, end, output_path, **kwargs):
        render.calls.append({"video_path": video_path, "output_path": output_path, **kwargs})
        Path(output_path).write_bytes(b"rendered")
        report = kwargs["layout_report"]
        report.update(fx_level="full", applied_warp=[], fx_plan=kwargs["fx"].plan, broll_applied=True,
                      face_track=kwargs["fx"].face_track)
        return True

    monkeypatch.setattr(clip_service_module, "create_optimized_clip", create_optimized_clip)
    monkeypatch.setattr(clip_service_module, "overlay_custom_captions", _never("overlay_custom_captions"))
    monkeypatch.setattr(clip_service_module, "resolve_upload_for_edit", AsyncMock(return_value=source))
    monkeypatch.setattr(clip_service_module, "rendered_clip_duration", lambda path, ranges, offsets=None: 7.0)
    monkeypatch.setattr(video_service, "plan_beats", _never("plan_beats"))
    monkeypatch.setattr(video_service, "resolve", _never("resolve"))
    edits.service.task_repo.get_task_by_id = AsyncMock(return_value={
        "id": "task-1", "source_url": "upload://source.mp4", "source_type": "video_url", "user_id": None,
        "font_family": None, "font_size": 30, "font_color": "#FFFFFF", "caption_template": "marker_circle",
    })
    edits.service.cache_repo.get_cache = AsyncMock(return_value=None)
    edits.service.video_service.prepare_source = AsyncMock(side_effect=lambda path: path)
    edits.service._load_task_source_settings = AsyncMock(return_value={"output_format": "vertical",
                                                                       "add_subtitles": True})
    edits.service._media_limits = AsyncMock(return_value=(None, None))
    return render


def _never(name):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"{name} must not be called")
    return fail


async def test_caption_edit_of_an_fx_clip_is_one_render_with_the_plan_replayed(edits, monkeypatch):
    clip = edits.tmp / "fx.mp4"
    clip.write_bytes(b"clip")
    save_clip_source_ranges(clip, [(20.0, 23.3)])
    track = {"version": 1, "space": "picture", "fps": 6.0, "samples": [[0.0, [[0.4, 0.2, 0.2, 0.2, 0.9]]]]}
    plan = _plan(3.3, [
        # "dollars" (A 21.5) and "massive" (A 22.7) accents; the edit renames "massive" to "wild".
        {**ACCENT, "id": "b1.accent", "start": 1.5, "end": 1.74, "text": "dollars", "beat": "b1"},
        {**ACCENT, "id": "b2.accent", "start": 2.7, "end": 2.94, "text": "massive", "beat": "b2"},
        SFX | {"start": 2.7, "end": 2.7},
    ])
    save_clip_edit_fx(clip, style={"intensity": "bold"}, beats=None, plan=plan.model_dump(mode="json"),
                      face_track=track, fx_level="full", applied_warp=[])
    edits.add("c1", clip)
    render = _fake_render(edits, monkeypatch)

    result = await edits.service.update_clip_captions(
        "task-1", "c1", "we made one hundred thousand dollars in a single wild day", "middle",
        ["dollars"], font_size=40, position_y=0.6,
    )

    (call,) = render.calls                                  # one pass, no overlay_custom_captions
    assert call["add_subtitles"] is True and call["caption_template"] == "marker_circle"
    assert call["fx"].face_track == track                   # reused, no analysis (AR-M10)
    override = call["caption_override"]
    assert override["position_y"] == 0.6 and override["font_size"] == 40
    assert override["highlight_words"] == ["dollars"]
    assert [w["text"] for w in override["words"]][-2:] == ["wild", "day"]
    assert override["words"][5]["start"] == pytest.approx(1.5)          # edited words keep spoken timings
    assert call["fx"].words == override["words"]
    accents = [c.text for c in call["fx"].plan.cues if c.kind == "accent"]
    assert accents == ["dollars"]                          # "massive" was edited out
    assert [c.kind for c in call["fx"].plan.cues if c.kind != "accent"] == ["sfx"]
    (update,) = edits.updates
    out = Path(update["file_path"])
    assert update["text"].endswith("wild day") and update["fx_meta"]["fx_level"] == "full"
    fx = load_clip_edit_fx(out)
    assert fx["style"] == {"intensity": "bold"} and fx["face_track"]["samples"]
    assert [c["text"] for c in fx["plan"]["cues"] if c["kind"] == "accent"] == ["dollars"]
    assert load_clip_caption_settings(out) == {
        "font_size": 40, "position": "middle", "position_y": 0.6, "highlight_words": ["dollars"],
    }
    assert "vertical_layout" not in sidecar(out) or sidecar(out)["vertical_layout"]
    assert result["id"] == "c1"


async def test_caption_edit_position_defaults_follow_the_named_position(edits, monkeypatch):
    clip = edits.tmp / "fx.mp4"
    clip.write_bytes(b"clip")
    save_clip_source_ranges(clip, [(20.0, 23.3)])
    save_clip_edit_fx(clip, style={}, beats=None, plan=_plan(3.3, []).model_dump(mode="json"),
                      face_track=None, fx_level="safe", applied_warp=[])
    edits.add("c1", clip)
    render = _fake_render(edits, monkeypatch)

    await edits.service.update_clip_captions("task-1", "c1", "", "top", [])

    (call,) = render.calls
    assert call["caption_override"]["position_y"] == 0.18
    assert call["caption_override"]["font_size"] == 30            # the task's size
    assert call["caption_override"]["words"] == []                # empty text: no captions


async def test_legacy_caption_edit_keeps_the_two_pass_path(edits, monkeypatch):
    clip = edits.tmp / "legacy.mp4"
    clip.write_bytes(b"clip")
    save_clip_source_ranges(clip, [(20.0, 23.3)])
    edits.add("c1", clip, fx=False)
    _fake_render(edits, monkeypatch)
    passes = []

    def clean_render(video_path, start, end, output_path, **kwargs):
        passes.append(("clean", kwargs.get("add_subtitles"), "fx" in kwargs))
        Path(output_path).write_bytes(b"clean")
        return True

    def overlay(clean_path, output_dir, *args, **kwargs):
        passes.append(("overlay",))
        out = Path(output_dir) / "caption_x.mp4"
        out.write_bytes(b"captioned")
        return out

    monkeypatch.setattr(clip_service_module, "create_optimized_clip", clean_render)
    monkeypatch.setattr(clip_service_module, "overlay_custom_captions", overlay)

    await edits.service.update_clip_captions("task-1", "c1", "edited", "bottom", [])

    assert passes == [("clean", False, False), ("overlay",)]
    assert load_clip_edit_fx(Path(edits.updates[0]["file_path"])) is None
    assert "fx_meta" not in edits.updates[0]
