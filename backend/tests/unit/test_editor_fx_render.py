"""The studio's dynamic-edits export (``editor_fx_render``, FINAL §14.1, WP15b).

- scene fixtures: Python reproduces ``shared/fx/fixtures/scene`` (fx-core's ``studioScene`` checks the same files);
- the face track and the matte follow the studio's segment cuts (pure resampling, and real-ffmpeg frame sampling of
  the matte graph);
- real renders (ffmpeg + deno): cue timing across cuts and repeats, volume and mute without a loudness stage;
- the fallback ladder: full → full without B-roll → safe → none, cancel, the kill switch, the warp A/V retry.
"""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src import editor_fx_render as studio
from src.edit_fx.contracts import FxPlan
from src.editor_document import EditDocument, render_document
from src.media import fx_chain
from src.media_storage import EDITOR_OPTIONAL_MEDIA_NAMES, MATTE_VERSION_MARKER
from tests.unit.fx_media.fx_chain_kit import make_source, requires_ffmpeg, requires_media

BACKEND = Path(__file__).resolve().parents[2]
SCENE_FIXTURES = BACKEND.parent / "shared" / "fx" / "fixtures" / "scene"
FPS = 30


def load_generator():
    spec = importlib.util.spec_from_file_location("fx_gen_scene", BACKEND / "scripts" / "fx" / "gen_scene_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def close(actual, expected, path="$"):
    """Deep equality with 1e-6 float tolerance (ints and bools exact)."""
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        assert actual == expected, path
    elif isinstance(expected, (int, float)):
        assert isinstance(actual, (int, float)) and not isinstance(actual, bool), path
        assert math.isclose(actual, expected, rel_tol=0, abs_tol=1e-6), f"{path}: {actual} != {expected}"
    elif isinstance(expected, list):
        assert isinstance(actual, (list, tuple)) and len(actual) == len(expected), f"{path}: length"
        for i, (a, e) in enumerate(zip(actual, expected)):
            close(a, e, f"{path}[{i}]")
    elif isinstance(expected, dict):
        assert isinstance(actual, dict) and set(actual) == set(expected), f"{path}: keys"
        for key in expected:
            close(actual[key], expected[key], f"{path}.{key}")
    else:
        raise AssertionError(f"{path}: unexpected {type(expected)}")


def doc(segments, cues=(), **changes) -> EditDocument:
    data = {
        "segments": [{"id": sid, "start": a, "end": b} for sid, a, b in segments],
        "captions": {"enabled": False, "template": "default"},
        "framing": {"aspect": "original", "fit": "cover"},
        "fx": {"style": {"intensity": "bold"}, "seed": 3, "cues": list(cues)},
    }
    fx_changes = changes.pop("fx", {})
    data["fx"].update(fx_changes)
    data.update(changes)
    return EditDocument.model_validate(data)


def cue(kind, cue_id, start, end=None, **fields):
    return {"kind": kind, "id": cue_id, "start": start, "end": start if end is None else end, "seed": 5, **fields}


# ----------------------------------------------------------------------------- scene fixtures (G2)

def scene_cases():
    return sorted(SCENE_FIXTURES.glob("*.json"))


def test_scene_fixtures_cover_the_studio_cases():
    names = {p.stem for p in scene_cases()}
    assert len(names) >= 20
    for name in ("repeated_segment", "reordered_segments", "face_track_follows_cut", "behind_with_matte",
                 "warp_across_cuts", "caption_placements", "square_contain_framing", "broll_windows"):
        assert name in names


@pytest.mark.parametrize("path", scene_cases(), ids=lambda p: p.stem)
def test_python_reproduces_scene_fixture(path):
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert fixture["family"] == "scene" and fixture["case"] == path.stem and fixture["fn"] == "studio_scene"
    inp = fixture["input"]
    document = EditDocument.model_validate(inp["document"])
    scene = studio.studio_scene(document, source_w=inp["source"]["width"], source_h=inp["source"]["height"],
                                base=inp["base"], face_track=inp["faceTrack"], matte=inp["matte"],
                                broll=inp["broll"])
    close(json.loads(scene.model_dump_json()), fixture["expected"])


def test_committed_scene_fixtures_match_the_generator():
    for name, content in load_generator().build_outputs().items():
        assert (SCENE_FIXTURES / name).read_text(encoding="utf-8") == content, f"{name}: run gen_scene_fixtures.py"


def test_scene_generator_refuses_to_overwrite_without_accept(tmp_path, capsys):
    generator = load_generator()
    assert generator.main(["--out", str(tmp_path)]) == 0
    target = tmp_path / "single_segment.json"
    target.write_text(target.read_text().replace('"seed": 2961888143', '"seed": 1'), encoding="utf-8")
    (tmp_path / "old_case.json").write_text("{}", encoding="utf-8")
    assert generator.main(["--out", str(tmp_path), "--check"]) == 1
    assert generator.main(["--out", str(tmp_path)]) == 1
    assert "Refusing" in capsys.readouterr().err
    assert generator.main(["--out", str(tmp_path), "--accept"]) == 0
    assert not (tmp_path / "old_case.json").exists()
    assert generator.main(["--out", str(tmp_path), "--check"]) == 0


# ----------------------------------------------------------------------------- face track and matte follow the cuts

def drifting_track(duration=6.0, fps=6.0):
    """x = 0.1 + 0.1 * t (S), so the S time a sample came from is readable from its x."""
    return {"version": 1, "space": "picture", "fps": fps,
            "samples": [[round(k / fps, 6), [[round(0.1 + 0.1 * k / fps, 6), 0.2, 0.2, 0.2, 0.9]]]
                        for k in range(int(duration * fps) + 1)]}


def test_face_track_is_resampled_onto_edit_time_through_the_cuts():
    segments = [("late", 3.0, 5.0), ("early", 0.5, 2.0), ("again", 0.5, 1.0)]
    track = studio.resample_face_track(drifting_track(), segments)
    samples = dict((t, rows) for t, rows in track["samples"])
    assert track["space"] == "picture" and track["fps"] == 6
    for e, (_, s) in ((t, studio.locate(segments, t)) for t in samples):
        assert samples[e][0][0] == pytest.approx(0.1 + 0.1 * s, abs=1e-6), e
    # A sample on each side of each cut: the face jumps with the picture instead of sliding across the cut.
    assert samples[1.999][0][0] == pytest.approx(0.1 + 0.1 * 4.999, abs=1e-6)
    assert samples[2.0][0][0] == pytest.approx(0.1 + 0.1 * 0.5, abs=1e-6)
    assert samples[3.5][0][0] == pytest.approx(0.1 + 0.1 * 0.5, abs=1e-6)
    assert max(samples) <= 4.0


def test_face_track_gaps_are_kept_and_unreadable_tracks_are_dropped():
    track = drifting_track()
    track["samples"] = [[t, rows if not (1.0 <= t < 2.0) else []] for t, rows in track["samples"]]
    resampled = dict((t, rows) for t, rows in studio.resample_face_track(track, [("a", 0.0, 3.0)])["samples"])
    assert resampled[1.5] == []
    assert resampled[0.5] and resampled[2.5]
    assert studio.resample_face_track({"version": 1, "samples": "nope"}, [("a", 0.0, 1.0)]) is None
    assert studio.resample_face_track(None, [("a", 0.0, 1.0)]) is None


def test_session_face_track_is_used_only_on_its_own_base():
    document = doc([("a", 0.0, 2.0)], [cue("zoom", "z", 0.2, 0.8, mode="punch", scale=1.2, anchor="face")])
    face = {"base": "layout", "track": drifting_track()}
    on_layout = studio.studio_scene(document, source_w=360, source_h=640, base="layout", face_track=face)
    on_clean = studio.studio_scene(document, source_w=360, source_h=640, base="clean", face_track=face)
    assert on_layout.face_track is not None and on_layout.face_track.samples
    # No usable track: an empty one, so the export falls back like the preview instead of analysing.
    assert on_clean.face_track is not None and on_clean.face_track.samples == ()


def test_matte_windows_follow_the_behind_cues_through_the_cuts():
    document = doc([("b", 2.0, 4.0), ("a", 0.0, 1.0)], [cue("accent", "bh", 0.5, 2.5, variant="behind", text="NO")],
                   captions={"enabled": True, "template": "behind_head"})
    with_matte = studio.studio_scene(document, source_w=360, source_h=640, base="clean", matte=True)
    assert [(round(w.start, 6), round(w.end, 6), w.person) for w in with_matte.matte_windows] == [
        (0.0, 0.5, True), (2.5, 3.0, True)]
    without = studio.studio_scene(document, source_w=360, source_h=640, base="clean", matte=False)
    assert all(not w.person for w in without.matte_windows)


def test_matte_filter_cuts_like_the_picture_and_frames_like_the_geometry():
    geometry, rect = studio.output_geometry(
        EditDocument.model_validate({"segments": [{"id": "s", "start": 0, "end": 1}],
                                     "framing": {"aspect": "square", "fit": "contain"}}).framing, 1920, 1080, 1080, 1080)
    assert rect == pytest.approx((0.0, 236 / 1080, 1.0, 608 / 1080))   # even pixel rounding, like frameRect
    single = studio.matte_filter_for([("a", 1.0, 2.0)], geometry, 1920, 1080)
    assert single.startswith(
        "scale=in_range=tv:out_range=pc,format=gray,fps=30,trim=start=1.0:end=2.0,setpts=PTS-STARTPTS,")
    assert single.endswith(geometry) and "scale=1920:1080" in single
    multi = studio.matte_filter_for([("a", 1.0, 2.0), ("b", 0.0, 0.5)], geometry, 1920, 1080)
    assert "split=2" in multi and "concat=n=2:v=1:a=0" in multi


def time_coded_matte(path: Path, *, duration=6.0, w=180, h=320):
    """A grey matte whose luma is 40 * t (S seconds), encoded like ``matte_for_video`` (4:2:0 H.264, tv range)."""
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=15:d={duration}",
        "-vf", "format=gray,geq=lum='min(255,40*T)',scale=out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", "-g", "15", "-pix_fmt", "yuv420p",
        "-color_range", "tv", str(path),
    ], check=True, capture_output=True)
    return path


@requires_ffmpeg
def test_matte_follows_segment_cuts_frame_by_frame(tmp_path):
    """AR-B4: the matte takes the same per-segment trims as [0:v], so at every E frame it shows the S moment."""
    matte = time_coded_matte(tmp_path / "matte.mp4")
    segments = [("late", 3.0, 5.0), ("early", 0.5, 2.0), ("again", 0.5, 1.0)]
    framing = EditDocument.model_validate({"segments": [{"id": "s", "start": 0, "end": 1}],
                                           "framing": {"aspect": "original", "fit": "cover"}}).framing
    geometry, _ = studio.output_geometry(framing, 360, 640, 360, 640)
    graph = (f"[0:v]{studio.matte_filter_for(segments, geometry, 360, 640, '30')},"
             f"scale=360:640:flags=bilinear,fps={FPS},format=gray[out]")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(matte), "-filter_complex", graph, "-map", "[out]",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=True, capture_output=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 640, 360)
    assert len(frames) == pytest.approx(4.0 * FPS, abs=1)
    for index in range(0, len(frames), 5):
        _, s = studio.locate(segments, index / FPS)
        assert abs(float(frames[index, 320, 180]) - 40 * s) <= 8, (index, s, frames[index, 320, 180])


# ----------------------------------------------------------------------------- inputs bound around a render

async def test_studio_fx_inputs_restore_only_a_matching_matte_and_clean_up(tmp_path, monkeypatch):
    calls = []

    class Storage:
        def restore_editor_media(self, directory, **kwargs):
            calls.append(kwargs)
            (directory / "matte.mp4").write_bytes(b"m")
            (directory / MATTE_VERSION_MARKER).write_text(kwargs["matte_version"])

    async def reaction(cue, scratch):
        path = Path(scratch) / f"{cue.id}.mp4"
        path.write_bytes(b"gif")
        return path

    monkeypatch.setattr("src.edit_fx.reactions.ensure_reaction_file", reaction)
    document = doc([("a", 0.0, 2.0)], [cue("accent", "bh", 0.5, 1.5, variant="behind", text="NO"),
                                       cue("reaction", "rx", 0.2, 1.0, query="wow", url="https://media.giphy.com/x.mp4")])
    asset = {"mediaVersion": "v1", "hasLayout": True, "matteVersion": "m7", "matteBase": "layout",
             "faceTrack": {"base": "layout", "track": drifting_track()}}
    async with studio.studio_fx_inputs(document, asset, tmp_path, storage=Storage()) as inputs:
        assert studio.bound_inputs() is inputs
        assert inputs.matte_path == tmp_path / "matte.mp4" and inputs.matte_base == "layout"
        assert inputs.face_track["base"] == "layout"
        assert inputs.reaction_files["rx"].read_bytes() == b"gif"
        scratch = inputs.reaction_files["rx"].parent
    assert calls == [{"has_layout": True, "version": "v1", "matte_version": "m7"}]
    assert not scratch.exists()
    assert studio.bound_inputs() == studio.StudioFxInputs()

    # A copy whose marker names another version is never used.
    (tmp_path / MATTE_VERSION_MARKER).write_text("old")
    assert studio.verified_matte(tmp_path, "m7") is None
    assert studio.verified_matte(tmp_path, "old") == tmp_path / "matte.mp4"
    assert studio.MATTE_NAME in EDITOR_OPTIONAL_MEDIA_NAMES and studio.MATTE_VERSION_MARKER == MATTE_VERSION_MARKER


async def test_studio_fx_inputs_skip_the_matte_when_nothing_draws_behind(tmp_path):
    class Storage:
        def restore_editor_media(self, *args, **kwargs):
            raise AssertionError("no behind text, no matte download")

    document = doc([("a", 0.0, 2.0)], [cue("flash", "f", 0.5, 0.8)])
    async with studio.studio_fx_inputs(document, {"matteVersion": "m1"}, tmp_path, storage=Storage()) as inputs:
        assert inputs.matte_path is None


def test_reaction_files_follow_repeated_cues():
    document = doc([("a", 0.0, 2.0), ("again", 0.0, 2.0)],
                   [cue("reaction", "rx", 0.2, 1.0, query="wow", url="https://media.giphy.com/x.mp4")])
    request = studio.studio_request(document, segments=studio._segments(document), out_w=360, out_h=640,
                                    source_w=360, source_h=640, base="clean",
                                    inputs=studio.StudioFxInputs(reaction_files={"rx": Path("/tmp/rx.mp4")}))
    assert request.reaction_files == {"rx": Path("/tmp/rx.mp4"), "rx@1": Path("/tmp/rx.mp4")}
    assert request.loudness == "none" and request.voice_gain == 1.0


def test_request_volume_mute_and_caption_look():
    document = doc([("a", 0.0, 2.0)], [cue("flash", "f", 0.5, 0.8)], volume=0.4,
                   captions={"enabled": True, "template": "slam", "size": 60, "y": 0.6},
                   textTransforms={"captions": {"x": 0, "y": 0.1, "scale": 1.5}},
                   words=[{"id": "w", "start": 0.1, "end": 0.6, "text": "go"}])
    request = studio.studio_request(document, segments=studio._segments(document), out_w=1080, out_h=1920,
                                    source_w=1080, source_h=1920, base="clean")
    assert request.voice_gain == pytest.approx(0.4)
    assert request.caption is not None and request.caption.y == pytest.approx(0.7)
    assert request.caption.size_px == 90
    assert request.words == [{"id": "w", "start": 0.1, "end": 0.6, "text": "go", "highlight": False, "run": "a"}]
    muted = studio.studio_request(document.model_copy(update={"muted": True}), segments=[("a", 0.0, 2.0)],
                                  out_w=1080, out_h=1920, source_w=1080, source_h=1920, base="clean")
    assert muted.voice_gain == 0.0
    legacy = studio.studio_request(document.model_copy(update={"captions": document.captions.model_copy(
        update={"template": "hormozi"})}), segments=[("a", 0.0, 2.0)], out_w=1080, out_h=1920, source_w=1080,
        source_h=1920, base="clean")
    assert legacy.caption is None


def test_request_and_scene_carry_the_documents_progress_colour():
    custom = doc([("a", 0.0, 2.0)], fx={"progressBar": True, "progressColor": "#22cc88"})
    request = studio.studio_request(custom, segments=studio._segments(custom), out_w=360, out_h=640,
                                    source_w=360, source_h=640, base="clean")
    assert request.progress_color == "#22cc88"
    assert studio.studio_scene(custom, source_w=360, source_h=640, base="clean").progress_color == "#22cc88"
    default = doc([("a", 0.0, 2.0)], fx={"progressBar": True})
    assert studio.studio_scene(default, source_w=360, source_h=640, base="clean").progress_color == "#FF7A1A"
    # Generation requests keep the default; a malformed value never reaches the drawer.
    assert fx_chain.FxRequest(plan=request.plan, words=[], caption=None).progress_color == fx_chain.FX_PROGRESS_COLOR
    assert fx_chain._progress_color("orange") == fx_chain.FX_PROGRESS_COLOR


def test_caption_layer_x_moves_fx_captions_and_own_transforms_replace_it():
    document = doc([("a", 0.0, 2.0)], captions={"enabled": True, "template": "slam", "y": 0.6},
                   textTransforms={"captions": {"x": 0.15, "y": 0.1, "scale": 2.0}},
                   captionTransforms={'["a","w"]': {"x": -0.05, "y": 0.0, "scale": 1.0}},
                   words=[{"id": "w", "start": 0.1, "end": 0.6, "text": "go"}])
    style = studio.studio_caption_style(document, 1080, 1920)
    assert style.x == pytest.approx(0.15)
    (own,) = style.placements
    assert style.x + own.x == pytest.approx(-0.05)          # the caption's own x, not layer + own
    assert style.y + own.y == pytest.approx(0.6)
    assert own.scale * 2.0 == pytest.approx(1.0)
    plain = doc([("a", 0.0, 2.0)], captions={"enabled": True, "template": "slam"})
    assert studio.studio_caption_style(plain, 1080, 1920).x == 0


# ----------------------------------------------------------------------------- real renders (ffmpeg + deno)

def session(tmp_path, *, duration=6.0, audio="sine", video="0x204080") -> Path:
    directory = tmp_path / "session"
    directory.mkdir()
    make_source(directory / "clean.mp4", w=360, h=640, dur=duration, video=video, audio=audio)
    return directory


def rgb_frames(path: Path, w=360, h=640) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w, 3)


def pcm(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le",
                          "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def rms_db(samples: np.ndarray) -> float:
    return 20 * math.log10(max(1e-9, float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))))


@requires_media
def test_fx_export_across_cuts_and_repeats_has_cue_timing_right(tmp_path):
    """A red flash at S 1.0 plays wherever its source moment plays: once per occurrence, at the right E frame."""
    directory = session(tmp_path)
    segments = [("late", 3.0, 5.0), ("early", 0.5, 2.0), ("again", 0.8, 1.6)]
    document = doc(segments, [cue("flash", "f", 1.0, 1.3, color="#FF0000", peak=1.0)])
    outcome = render_document(directory, "job1", document, "tiktok")
    assert outcome == {"level": "full", "applied_warp": []}
    frames = rgb_frames(directory / "export-job1.mp4")
    assert len(frames) == pytest.approx(4.3 * FPS, abs=1)
    red = [i for i, f in enumerate(frames) if f[..., 0].mean() > 150 and f[..., 2].mean() < 110]
    # E 2.5 (S 1.0 inside "early") and E 3.7 (S 1.0 inside "again"): each the first frames of a decaying flash.
    expected = {75, 76, 77, 111, 112, 113}
    assert expected <= set(red)
    assert all(70 <= i < 84 or 106 <= i < 120 for i in red), red
    assert not (directory / ".export-job1.mp4").exists()
    assert not list(directory.glob(".fx-studio-*"))


@requires_media
def test_studio_export_level_follows_the_volume_slider_and_mute_adds_no_lift(tmp_path):
    directory = session(tmp_path)
    segments = [("a", 0.0, 3.0)]
    picture_only = [cue("flash", "f", 1.0, 1.2)]
    render_document(directory, "full", doc(segments, picture_only), "tiktok")
    render_document(directory, "half", doc(segments, picture_only, volume=0.5), "tiktok")
    full, half = pcm(directory / "export-full.mp4"), pcm(directory / "export-half.mp4")
    window = slice(int(0.5 * 48000), int(2.5 * 48000))
    assert rms_db(half[window]) - rms_db(full[window]) == pytest.approx(-6.02, abs=0.5)

    # Muted with an SFX: the voice is gone and the SFX sits at its planned bus level (no loudness stage).
    with_sfx = [*picture_only, cue("sfx", "s", 1.5, sound="boom", gain_db=0)]
    document = doc(segments, with_sfx, muted=True)
    render_document(directory, "muted", document, "tiktok")
    muted = pcm(directory / "export-muted.mp4")
    assert rms_db(muted[: int(1.4 * 48000)]) < -60
    from src.media import fx_audio

    plan = studio.studio_plan(document, studio._segments(document))
    bus = tmp_path / "bus.wav"
    from src.edit_fx.warp import identity_warp

    assert fx_audio.build_bus(plan, words_e=[], warp=identity_warp(3.0), out_duration=3.0, path=bus)
    expected = pcm(bus)   # decoded the same way as the export
    sfx_window = slice(int(1.5 * 48000), int(2.3 * 48000))
    assert rms_db(muted[sfx_window]) == pytest.approx(rms_db(expected[sfx_window]), abs=1.0)


@requires_media
def test_square_studio_export_with_a_landscape_source_keeps_fx_in_frame(tmp_path):
    directory = tmp_path / "session"
    directory.mkdir()
    make_source(directory / "clean.mp4", w=640, h=360, dur=2.0, video="0x204080")
    document = doc([("a", 0.0, 2.0)], [cue("flash", "f", 0.5, 0.8, color="#FF0000", peak=1.0)],
                   framing={"aspect": "square", "fit": "contain"})
    assert render_document(directory, "sq", document, "tiktok")["level"] == "full"
    frames = rgb_frames(directory / "export-sq.mp4", 1080, 1080)
    assert frames[16, 5, 5, 0] > 150 and frames[16, 540, 540, 0] > 150   # the flash covers the letterbox too


# ----------------------------------------------------------------------------- fallback ladder

class ChainSpy:
    """Stands in for run_fx_chain: fails the listed attempts, records every call, writes a file otherwise."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        error = self.errors.pop(0) if self.errors else None
        if error is None:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=360x640:r=30:d=1",
                            "-c:v", "libx264", "-preset", "ultrafast", str(kwargs["output_path"])], check=True)
        mode = kwargs["mode"]
        return fx_chain.FxResult(ok=error is None, width=360, height=640, level=mode, out_duration=1.0,
                                 face_track=None, applied_warp=[{"e0": 0, "e1": 1, "o0": 0, "rate": 1, "hold": 0}]
                                 if mode == "full" and kwargs["request"].plan.has_warp else [],
                                 timings={}, error=error)


def broll_document(segments, cues):
    return doc(segments, cues, broll=[{"id": "r", "start": 0.5, "end": 1.5, "footage": {
        "id": 1, "width": 360, "height": 640, "duration": 5,
        "fileUrl": "https://videos.pexels.com/video-files/1/x.mp4"}}])


@requires_ffmpeg
def test_p1_failure_with_broll_retries_full_without_broll_then_safe_then_legacy(tmp_path, monkeypatch):
    directory = session(tmp_path, duration=3.0)
    footage = make_source(tmp_path / "broll.mp4", w=360, h=640, dur=3.0, video="0x00FF00", audio=None)
    document = broll_document([("a", 0.0, 2.0)], [cue("flash", "f", 0.5, 0.8), cue("sfx", "s", 0.6, sound="boom")])
    spy = ChainSpy(["p1: exit 1: bad b-roll", "p2: exit 1", "p3: exit 1"])
    monkeypatch.setattr(fx_chain, "run_fx_chain", spy)
    outcome = render_document(directory, "job", document, "tiktok", {document.broll[0].footage.key: footage})
    assert outcome == {"level": "none", "applied_warp": []}
    assert [c["mode"] for c in spy.calls] == ["full", "full", "safe"]
    first, second, safe = spy.calls
    assert first["broll_inputs"] and first["request"].broll_windows
    assert second["broll_inputs"] == [] and second["request"].broll_windows == ()
    assert "[fxs_head]" == second["head_label"] and "overlay" not in second["head"]
    assert safe["subtitle_ass_path"] is not None and safe["request"].loudness == "none"
    # Level none is the legacy render: the clip still exists, with its voice.
    frames = rgb_frames(directory / "export-job.mp4")
    assert len(frames) == pytest.approx(2 * FPS, abs=1)
    assert rms_db(pcm(directory / "export-job.mp4")) > -40


@requires_ffmpeg
def test_legacy_level_drops_unreadable_broll_instead_of_failing_the_export(tmp_path, monkeypatch):
    directory = session(tmp_path, duration=3.0)
    footage = tmp_path / "broken.mp4"
    footage.write_bytes(b"not a video")
    document = broll_document([("a", 0.0, 2.0)], [cue("flash", "f", 0.5, 0.8)])
    monkeypatch.setenv("FX_RUNTIME", "off")
    spy = ChainSpy(["p1: exit 1: bad b-roll"])
    monkeypatch.setattr(fx_chain, "run_fx_chain", spy)
    outcome = render_document(directory, "job", document, "tiktok", {document.broll[0].footage.key: footage})
    assert outcome == {"level": "none", "applied_warp": []}
    assert [c["mode"] for c in spy.calls] == ["safe"]
    frames = rgb_frames(directory / "export-job.mp4")
    assert len(frames) == pytest.approx(2 * FPS, abs=1)
    assert not list(directory.glob(".fx-studio-*"))


@requires_ffmpeg
def test_safe_level_keeps_the_clip_with_fallback_captions_and_hook(tmp_path, monkeypatch):
    directory = session(tmp_path, duration=3.0)
    document = doc([("a", 0.0, 2.0)], [cue("bleep", "b", 0.5, 1.0)], hookTitle="Watch this",
                   captions={"enabled": True, "template": "slam"},
                   words=[{"id": "w", "start": 0.5, "end": 1.0, "text": "damn"}],
                   fx={"hookTitleSuppressed": True})
    spy = ChainSpy(["p2: exit 1", None])
    monkeypatch.setattr(fx_chain, "run_fx_chain", spy)
    assert render_document(directory, "job", document, "tiktok")["level"] == "safe"
    full, safe = spy.calls
    assert full["mode"] == "full" and safe["mode"] == "safe"
    assert safe["subtitle_ass_path"].name == "safe.ass"


def test_fallback_document_uses_the_legacy_template_and_censors_under_bleeps():
    document = doc([("a", 0.0, 2.0)], [cue("bleep", "b", 0.5, 1.0)], captions={"enabled": True, "template": "slam"},
                   words=[{"id": "w", "start": 0.5, "end": 1.0, "text": "damn"},
                          {"id": "x", "start": 1.0, "end": 1.5, "text": "ok"}])
    safe = studio._fallback_document(document, censor=True)
    assert safe.captions.template == "mrbeast"
    from src.edit_fx.censor import censor_text

    assert [w.text for w in safe.words] == [censor_text("damn"), "ok"] and censor_text("damn") != "damn"
    legacy = studio._fallback_document(document, censor=False)
    assert [w.text for w in legacy.words] == ["damn", "ok"]


@requires_ffmpeg
def test_cancel_stops_the_ladder(tmp_path, monkeypatch):
    directory = session(tmp_path, duration=2.0)
    spy = ChainSpy(["cancelled"])
    monkeypatch.setattr(fx_chain, "run_fx_chain", spy)
    with pytest.raises(InterruptedError):
        render_document(directory, "job", doc([("a", 0.0, 1.0)], [cue("flash", "f", 0.2, 0.4)]), "tiktok")
    assert len(spy.calls) == 1
    assert not (directory / "export-job.mp4").exists()


@requires_ffmpeg
def test_kill_switch_renders_at_level_safe(tmp_path, monkeypatch):
    directory = session(tmp_path, duration=2.0)
    monkeypatch.setenv("FX_RUNTIME", "off")
    spy = ChainSpy([None])
    monkeypatch.setattr(fx_chain, "run_fx_chain", spy)
    assert render_document(directory, "job", doc([("a", 0.0, 1.0)], [cue("flash", "f", 0.2, 0.4)]),
                           "tiktok")["level"] == "safe"
    assert [c["mode"] for c in spy.calls] == ["safe"]


@requires_ffmpeg
def test_fx_runtime_legacy_exports_through_the_legacy_render(tmp_path, monkeypatch):
    directory = session(tmp_path, duration=2.0)
    monkeypatch.setenv("FX_RUNTIME", "legacy")
    spy = ChainSpy([None])
    monkeypatch.setattr(fx_chain, "run_fx_chain", spy)
    outcome = render_document(directory, "job", doc([("a", 0.0, 1.0)], [cue("flash", "f", 0.2, 0.4)]), "tiktok")
    assert outcome == {"level": "none", "applied_warp": []}
    assert spy.calls == [] and (directory / "export-job.mp4").exists()


@requires_ffmpeg
def test_warped_render_with_av_mismatch_is_redone_with_an_identity_warp(tmp_path, monkeypatch):
    directory = session(tmp_path, duration=3.0)
    spy = ChainSpy([None, None])
    monkeypatch.setattr(fx_chain, "run_fx_chain", spy)
    monkeypatch.setattr(studio, "_av_mismatch", lambda path: 0.2)
    document = doc([("a", 0.0, 2.0)], [cue("freeze", "fr", 1.0, hold=0.5)])
    outcome = render_document(directory, "job", document, "tiktok")
    assert outcome == {"level": "full", "applied_warp": []}
    first, second = spy.calls
    assert first["request"].plan.has_warp and not second["request"].plan.has_warp


@requires_media
def test_a_real_warped_studio_export_reports_its_applied_warp(tmp_path):
    directory = session(tmp_path, duration=3.0)
    document = doc([("a", 0.0, 1.0), ("b", 1.5, 2.5)], [cue("freeze", "fr", 1.8, hold=0.5)])
    outcome = render_document(directory, "warp", document, "tiktok")
    assert outcome["level"] == "full" and outcome["applied_warp"]
    frames = rgb_frames(directory / "export-warp.mp4")
    assert len(frames) == pytest.approx((2.0 + 0.5) * FPS, abs=1)
    assert studio._av_mismatch(directory / "export-warp.mp4") <= studio.AV_TOLERANCE


def test_plan_carries_the_documents_globals_on_edit_time():
    document = doc([("b", 2.0, 4.0), ("a", 0.0, 1.0)], [cue("flash", "f", 0.5, 0.7), cue("flash", "g", 3.0, 3.2)],
                   fx={"grade": "noir", "grain": 0.4, "progressBar": True, "loopEnding": "crossfade",
                       "hookTitleSuppressed": True, "disabled": ["shake"]})
    plan = studio.studio_plan(document, studio._segments(document))
    assert isinstance(plan, FxPlan)
    assert plan.edit_duration == 3.0 and [(c.id, c.start) for c in plan.cues] == [("g", 1.0), ("f", 2.5)]
    assert (plan.grade, plan.grain, plan.progress_bar, plan.loop_ending, plan.hook_title_suppressed) == (
        "noir", 0.4, True, "crossfade", True)


def split_matte(path: Path, *, duration=6.0, w=180, h=320):
    """The person is the left half before S 2.0 and the right half after (grey H.264, tv range, 15 fps)."""
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=15:d={duration}",
        "-vf", "format=gray,geq=lum='if(lt(T\\,2)\\,if(lt(X\\,W/2)\\,255\\,0)\\,if(lt(X\\,W/2)\\,0\\,255))',"
               "scale=out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", "-g", "15", "-pix_fmt", "yuv420p",
        "-color_range", "tv", str(path),
    ], check=True, capture_output=True)
    return path


@requires_media
def test_behind_text_cutout_follows_the_right_moment_after_a_studio_cut(tmp_path):
    """The session matte goes through the same cuts as the picture: after reordering, the person that covers the
    behind text is the one of the S moment on screen (AR-B4)."""
    directory = session(tmp_path)
    matte = split_matte(directory / "matte.mp4")
    (directory / MATTE_VERSION_MARKER).write_text("m1")
    segments = [("late", 3.0, 5.0), ("early", 0.0, 2.0)]
    document = doc(segments, [cue("accent", "bh", 0.1, 4.9, variant="behind", text="WOW")],
                   captions={"enabled": False, "template": "behind_head"})
    with studio.bind_inputs(studio.StudioFxInputs(matte_path=matte, matte_base="clean")):
        outcome = render_document(directory, "behind", document, "tiktok")
    assert outcome["level"] == "full"
    frames = rgb_frames(directory / "export-behind.mp4")

    def text_pixels(frame):
        band = frame[: 640 // 2]
        white = (band.min(axis=2) > 200)
        return int(white[:, :180].sum()), int(white[:, 180:].sum())

    # E 1.0 plays S 4.0: the person is the right half, so the text shows on the left only.
    left, right = text_pixels(frames[30])
    assert left > 200 and right < left / 10, (left, right)
    # E 3.0 plays S 1.0: the person is the left half, so the text shows on the right only.
    left, right = text_pixels(frames[90])
    assert right > 200 and left < right / 10, (left, right)
