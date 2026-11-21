"""Legacy golden pins: today's behaviour, frozen before dynamic edits touch the hot files.

Dynamic edits must leave the legacy path (intensity off, one of the seven legacy caption
templates) byte-identical: the same ASS files, ffmpeg argv, call kwargs, sidecar keys,
API JSON, SQL text, AI-edit prompt and editor document dump. Each test computes the
current value and compares it with ``tests/fixtures/legacy_pins/expected/``.

The expected files were captured on unchanged code (``bedf6ce4``). Regenerate them only
for an intended legacy change, with ``scripts/fx/capture_legacy_pins.py --accept``; the
script runs this module with ``LEGACY_PINS_WRITE=1``, which writes instead of asserting.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src import video_utils
from src.caption_templates import CAPTION_TEMPLATES, get_template, get_template_info
from src.clip_source_map import clip_source_map_path, save_clip_broll, save_clip_source_ranges
from src import editor_document
from src.editor_document import EditDocument
from src.media import captions, reframing
from src.media.ffmpeg import ffmpeg_escape_filter_value
from src.repositories.task_repository import TaskRepository
from src.services import ai_edit_service, clip_service as clip_service_module, video_service
from src.services.task_service import TaskService
from src.services.video_service import VideoService
from tests.unit.test_task_service import build_task_service

BACKEND = Path(__file__).resolve().parents[2]
PINS = BACKEND / "tests" / "fixtures" / "legacy_pins"
EXPECTED = PINS / "expected"
WRITE = os.environ.get("LEGACY_PINS_WRITE") == "1"
CAPTURE_HINT = "Intended legacy change? Re-capture with scripts/fx/capture_legacy_pins.py --accept."

LEGACY_TEMPLATES = ("default", "hormozi", "mrbeast", "minimal", "tiktok", "neon", "podcast")
# Two ranges in source order (default crossfade), and the same ranges with planned offsets.
RANGES = [(9.9, 19.2), (20.9, 28.9)]
OFFSETS = [0.0, 9.26]
HOOK = "I lost forty grand in one night"


# --------------------------------------------------------------------------- pin storage


def _jsonable(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def pin_json(name: str, key: str, value) -> None:
    """Compare ``value`` with ``expected/<name>.json[key]`` (or record it in write mode)."""
    path = EXPECTED / f"{name}.json"
    value = _jsonable(value)
    if WRITE:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data[key] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    assert path.exists(), f"Missing pin file {path.name}. {CAPTURE_HINT}"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert key in data, f"Missing pin {name}[{key}]. {CAPTURE_HINT}"
    assert value == data[key], f"Legacy pin {name}[{key}] changed. {CAPTURE_HINT}"


def pin_text(name: str, text: str) -> None:
    """Compare ``text`` byte for byte with ``expected/<name>.txt``."""
    path = EXPECTED / f"{name}.txt"
    if WRITE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    assert path.exists(), f"Missing pin file {path.name}. {CAPTURE_HINT}"
    assert text == path.read_text(encoding="utf-8"), f"Legacy pin {path.name} changed. {CAPTURE_HINT}"


class Normalizer:
    """Make recorded calls machine-independent: temp paths, uuids and objects become tokens."""

    RENDER_DIR = re.compile(r"/[^\s'\"=:]*?supoclip_render_[A-Za-z0-9_]+")
    UNIQUE = re.compile(r"_[0-9a-f]{12}\.mp4")

    def __init__(self, tmp_path: Path):
        self.roots = []
        for root, token in ((tmp_path, "<TMP>"), (BACKEND, "<BACKEND>")):
            self.roots += [(str(root), token), (ffmpeg_escape_filter_value(str(root)), token)]

    def __call__(self, value):
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, str):
            value = self.RENDER_DIR.sub("<RENDER>", value)
            for root, token in self.roots:
                value = value.replace(root, token)
            return self.UNIQUE.sub("_<UUID>.mp4", value)
        if isinstance(value, dict):
            return {str(key): self(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self(item) for item in value]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return f"<{type(value).__name__}>"


def _call(args, kwargs, norm: Normalizer) -> dict:
    return {"args": norm(list(args)), "kwargs": norm(dict(kwargs))}


@pytest.fixture
def norm(tmp_path):
    return Normalizer(tmp_path)


@pytest.fixture
def source_video(tmp_path, monkeypatch):
    """A source file whose cached transcript is the fixed fixture transcript."""
    path = tmp_path / "source.mp4"
    path.write_bytes(b"source")
    shutil.copyfile(PINS / "transcript.json", path.with_suffix(".transcript_cache.json"))
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    monkeypatch.setattr("src.media.timeline.ffprobe_duration", lambda _path: 60.0)
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_duration", lambda _path: 0.0)
    return path


# --------------------------------------------------------------------------- caption templates


def test_caption_template_ids_are_the_seven_legacy_templates():
    assert tuple(CAPTION_TEMPLATES) == LEGACY_TEMPLATES
    pin_json("caption_templates", "CAPTION_TEMPLATES.keys", list(CAPTION_TEMPLATES))
    pin_json("caption_templates", "CAPTION_TEMPLATES", CAPTION_TEMPLATES)


@pytest.mark.parametrize("template_id", [*LEGACY_TEMPLATES, "unknown-template"])
def test_get_template_snapshot(template_id):
    pin_json("caption_templates", f"get_template:{template_id}", get_template(template_id))


def test_template_info_and_route_json():
    pin_json("caption_templates", "get_template_info", get_template_info())

    from src.api.routes import media

    app = FastAPI()
    app.include_router(media.router)
    with TestClient(app) as client:
        response = client.get("/caption-templates")
    assert response.status_code == 200
    pin_text("caption_templates_route", response.text)
    pin_json("caption_templates", "GET /caption-templates", response.json())


# --------------------------------------------------------------------------- ASS subtitles


def _karaoke_template(template_id: str, karaoke: bool) -> dict:
    """The studio's karaoke toggle applied to a template (editor_caption_template's rule)."""
    template = dict(get_template(template_id))
    if karaoke:
        template["animation"] = "karaoke"
    elif template["animation"] == "karaoke":
        template["animation"] = "none"
    return template


def _ass_digest(source_video, tmp_path, template_id, ranges=RANGES, **kwargs) -> dict:
    output = tmp_path / f"{template_id}.ass"
    output.unlink(missing_ok=True)
    report: dict = {}
    ok = captions.build_assemblyai_ass_subtitles(
        source_video, ranges[0][0], ranges[-1][1], 1080, 1920, output,
        None, None, None, template_id, ranges,
        caption_layout_report=report,
        **kwargs,
    )
    text = output.read_text(encoding="utf-8") if output.exists() else ""
    assert str(tmp_path) not in text
    return {
        "ok": ok,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "dialogue_lines": sum(1 for line in text.splitlines() if line.startswith("Dialogue:")),
        "layout_report": report,
    }


@pytest.mark.parametrize("offsets", [False, True], ids=["crossfade", "offsets"])
@pytest.mark.parametrize("karaoke", [None, True, False], ids=["generated", "karaoke_on", "karaoke_off"])
@pytest.mark.parametrize("template_id", LEGACY_TEMPLATES)
def test_ass_sha256(source_video, tmp_path, template_id, karaoke, offsets):
    kwargs = {"hook_title": HOOK, "include_captions": True}
    if karaoke is not None:
        kwargs["template_override"] = _karaoke_template(template_id, karaoke)
    if offsets:
        kwargs["output_offsets"] = OFFSETS
    karaoke_id = {None: "generated", True: "karaoke_on", False: "karaoke_off"}[karaoke]
    key = f"{template_id}|{karaoke_id}|{'offsets' if offsets else 'crossfade'}"
    pin_json("ass_sha256", key, _ass_digest(source_video, tmp_path, template_id, **kwargs))


@pytest.mark.parametrize("template_id", LEGACY_TEMPLATES)
def test_ass_sha256_single_range(source_video, tmp_path, template_id):
    """A plain highlight: one keep range, no offsets, the template by name."""
    digest = _ass_digest(source_video, tmp_path, template_id, ranges=[RANGES[0]], hook_title=HOOK)
    pin_json("ass_sha256", f"{template_id}|single_range", digest)


EXTRA_ASS_CASES = {
    # A range with no spoken words: the hook title alone, and nothing at all without one.
    "no_words_hook": {"ranges": [(40.0, 50.0)], "hook_title": HOOK},
    "no_words_no_hook": {"ranges": [(40.0, 50.0)]},
    # The clip renderer's arguments when captions are off but the hook title burns.
    "hook_only": {"hook_title": HOOK, "include_captions": False},
    # A recap's point labels with the planned offsets.
    "point_labels": {
        "hook_title": HOOK, "output_offsets": OFFSETS,
        "point_labels": [{"text": "The loss", "start": 0.0}, {"text": "The fix", "start": 9.26}],
    },
    # Speaker framing's caption seam and a burned-in caption band in the source.
    "placement": {"hook_title": HOOK, "position_y_override": 0.31, "avoid_band": (0.7, 0.82)},
    # The caption PATCH: explicit words and highlights.
    "caption_words": {
        "caption_words": [
            {"text": "Edited", "start": 0.2, "end": 0.6},
            {"text": "caption", "start": 0.6, "end": 1.1},
            {"text": "words!", "start": 1.1, "end": 1.7},
        ],
        "highlight_words": ["caption"],
    },
    # The studio's server render: final style values and editor placement.
    "studio": {
        "hook_title": HOOK, "template_override": _karaoke_template("tiktok", True), "font_px_override": 70,
        "center_y_override": 1400, "emphasis_indices": [1, 4], "wrap_lines": True, "center_x_override": 520,
        "hook_editor_style": {"x": 0.0, "y": -0.04, "scale": 1.1, "base_px": 52, "start": 0.0, "end": 3.5},
    },
}


@pytest.mark.parametrize("case", sorted(EXTRA_ASS_CASES))
def test_ass_sha256_render_paths(source_video, tmp_path, case):
    kwargs = copy.deepcopy(EXTRA_ASS_CASES[case])
    pin_json("ass_sha256", f"extra|{case}", _ass_digest(source_video, tmp_path, "default", **kwargs))


# --------------------------------------------------------------------------- render_reframed_clip_ffmpeg argv

SPLIT_PLAN = {
    "mode": "split",
    "regions": {
        "left": {"tile_w": 540, "tile_h": 480, "tile_x": 100, "tile_y": 60},
        "right": {"tile_w": 540, "tile_h": 480, "tile_x": 1180, "tile_y": 64},
    },
}
PAN_PLAN = {"mode": "pan", "crop_w": 608, "crop_h": 1080, "x_expression": "if(lt(t,4),120,clip(640-(t-4)*40,0,1312))"}
VF_PLAN = ("crop=608:1080:656:0,scale=1080:1920:flags=lanczos,setsar=1", "vf")
COMPLEX_PLAN = (
    "[0:v]split=2[bg][fg];[bg]scale=1080:1920,boxblur=20[b];[fg]scale=1080:-2[f];[b][f]overlay=0:(H-h)/2[vout]",
    "complex",
)
REFRAME_BRANCHES = {
    "original": ("original", {}),
    "vertical_split": ("vertical_split", {"speaker_plan": SPLIT_PLAN}),
    "vertical_pan": ("vertical_pan", {"speaker_plan": PAN_PLAN}),
    "vertical_fit": ("vertical_fit", {}),
    "vertical_vf": ("vertical", {"vertical_plan": VF_PLAN}),
    "vertical_complex": ("vertical", {"vertical_plan": COMPLEX_PLAN}),
}


@pytest.fixture
def reframe(tmp_path, monkeypatch, norm):
    """Run render_reframed_clip_ffmpeg with every probe and ffmpeg call faked; returns the pin."""
    commands: list = []
    probe = {"size": (1920, 1080), "geometry": (1920, 1080, 30.0), "audio": True}

    def run(command, *args, **kwargs):
        commands.append(list(command))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def geometry(_path):
        if probe["geometry"] is None:
            return None
        width, height, fps = probe["geometry"]
        return SimpleNamespace(display_width=width, display_height=height, fps=fps)

    # The face passes the renderer runs itself when no plan is passed in. A case opts in by
    # giving their result (``detected=`` / ``built=``); every other case passes its plan
    # explicitly, so a call is a failure there.
    planners: dict = {"detected": None, "built": None, "calls": []}

    def planner(name):
        def fake(*args, **kwargs):
            if planners[name] is None:
                raise AssertionError("the pin passes its layout plan explicitly")
            planners["calls"].append({name: _call(copy.deepcopy(args), copy.deepcopy(kwargs), norm)})
            return copy.deepcopy(planners[name][0])
        return fake

    monkeypatch.setattr(reframing, "run_ffmpeg_command", run)
    monkeypatch.setattr(reframing, "ffprobe_video_size", lambda _path: probe["size"])
    monkeypatch.setattr(reframing, "ffprobe_has_audio", lambda _path: probe["audio"])
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _path: 30.0)
    monkeypatch.setattr(reframing, "probe_video_geometry", geometry)
    monkeypatch.setattr(reframing, "detect_speaker_reframe_plan", planner("detected"))
    monkeypatch.setattr(reframing, "build_vertical_filter_plan", planner("built"))

    source = tmp_path / "source.mp4"
    source.write_bytes(b"stage one")
    output = tmp_path / "final.mp4"
    ass = tmp_path / "captions.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    footage = []
    for name in ("broll_a.mp4", "broll_b.mp4"):
        footage.append(tmp_path / name)
        footage[-1].write_bytes(b"stock")
    overlays = [
        {"path": str(footage[0]), "start": 1.0, "duration": 3.0},
        {"path": str(footage[1]), "start": 8.0, "duration": 2.5, "offset": 1.25, "fade_in": False},
    ]

    def render(output_format, *, subs=False, broll=False, detected=None, built=None, **kwargs):
        commands.clear()
        planners.update(detected=detected, built=built, calls=[])
        output.unlink(missing_ok=True)
        if "size" in kwargs:
            probe["size"] = kwargs.pop("size")
        if "geometry" in kwargs:
            probe["geometry"] = kwargs.pop("geometry")
        if "audio" in kwargs:
            probe["audio"] = kwargs.pop("audio")
        result = reframing.render_reframed_clip_ffmpeg(
            source, output, output_format,
            **({"subtitle_ass_path": ass, "fonts_dir": fonts} if subs else {}),
            **({"broll_overlays": copy.deepcopy(overlays)} if broll else {}),
            **kwargs,
        )
        pin = {"argv": norm(commands), "result": list(result), "copied": output.exists()}
        if detected is not None or built is not None:
            pin["planner_calls"] = planners["calls"]
        return pin

    return render


@pytest.mark.parametrize("subs", [False, True], ids=["no_subs", "subs"])
@pytest.mark.parametrize("broll", [False, True], ids=["no_broll", "broll"])
@pytest.mark.parametrize("branch", sorted(REFRAME_BRANCHES))
def test_render_reframed_clip_ffmpeg_argv(reframe, branch, broll, subs):
    output_format, kwargs = REFRAME_BRANCHES[branch]
    pin = reframe(output_format, subs=subs, broll=broll, **copy.deepcopy(kwargs))
    assert pin["argv"] or pin["copied"]
    key = f"{branch}|{'broll' if broll else 'no_broll'}|{'subs' if subs else 'no_subs'}"
    pin_json("reframe_argv", key, pin)


ORIGINAL_CASES = {
    "4k_no_subs": {"size": (3840, 2160), "geometry": (3840, 2160, 30.0)},
    "4k_subs": {"size": (3840, 2160), "geometry": (3840, 2160, 30.0), "subs": True},
    "60fps_no_subs": {"geometry": (1920, 1080, 60.0)},
    "60fps_subs": {"geometry": (1920, 1080, 60.0), "subs": True},
    "ntsc_no_subs": {"geometry": (1920, 1080, 59.94)},
    "no_geometry_no_subs": {"geometry": None},
    "no_geometry_subs": {"geometry": None, "subs": True},
    "anamorphic_broll": {"size": (1440, 1080), "geometry": (1920, 1080, 25.0), "broll": True},
}


@pytest.mark.parametrize("case", sorted(ORIGINAL_CASES))
def test_render_reframed_clip_ffmpeg_original_argv(reframe, case):
    pin_json("reframe_argv", f"original_case|{case}", reframe("original", **copy.deepcopy(ORIGINAL_CASES[case])))


SPEAKER_TURNS = [{"speaker": "A", "start": 0.0, "end": 9.3}, {"speaker": "B", "start": 9.3, "end": 18.4}]
SPEAKER_WORDS = [{"text": "So", "start": 0.1, "end": 0.3, "speaker": "A"}]
# The renderer plans the layout itself: (output format, planner results, extra kwargs). Each
# planner result is wrapped in a 1-tuple so "no faces found" (None) can be pinned too.
SELF_PLANNED_CASES = {
    # The default vertical branch: the face-aware vf plan from build_vertical_filter_plan.
    "vertical_default_vf": ("vertical", {"built": (VF_PLAN + (0.71,),)}, {}),
    "vertical_default_complex": ("vertical", {"built": (COMPLEX_PLAN + (None,),)}, {}),
    "vertical_default_speaker_turns": (
        "vertical", {"built": (VF_PLAN + (0.71,),)},
        {"speaker_turns": SPEAKER_TURNS, "speaker_words": SPEAKER_WORDS},
    ),
    # Generation's split/pan formats detect the plan; no faces falls back to the default plan.
    "vertical_split_detected": ("vertical_split", {"detected": (SPLIT_PLAN,)}, {}),
    "vertical_pan_detected": ("vertical_pan", {"detected": (PAN_PLAN,)}, {}),
    "vertical_split_no_faces": (
        "vertical_split", {"detected": (None,), "built": (VF_PLAN + (0.71,),)}, {},
    ),
}


@pytest.mark.parametrize("subs", [False, True], ids=["no_subs", "subs"])
@pytest.mark.parametrize("broll", [False, True], ids=["no_broll", "broll"])
@pytest.mark.parametrize("case", sorted(SELF_PLANNED_CASES))
def test_render_reframed_clip_ffmpeg_self_planned_argv(reframe, case, broll, subs):
    output_format, planners, kwargs = copy.deepcopy(SELF_PLANNED_CASES[case])
    pin = reframe(output_format, subs=subs, broll=broll, **planners, **kwargs)
    assert pin["argv"] and pin["planner_calls"]
    key = f"self_planned|{case}|{'broll' if broll else 'no_broll'}|{'subs' if subs else 'no_subs'}"
    pin_json("reframe_argv", key, pin)


def test_render_reframed_clip_ffmpeg_silent_source(reframe):
    pin_json("reframe_argv", "vertical_vf|silent", reframe("vertical", subs=True, audio=False, vertical_plan=VF_PLAN))


# --------------------------------------------------------------------------- create_optimized_clip internals


@pytest.fixture
def optimized(source_video, tmp_path, monkeypatch, norm):
    """create_optimized_clip with every stage recorded; returns (run, calls)."""
    calls: dict = {}
    state = {"band": None, "fail_broll": False}

    def record(name, result):
        def spy(*args, **kwargs):
            calls.setdefault(name, []).append(_call(copy.deepcopy(args), copy.deepcopy(kwargs), norm))
            return result(*args, **kwargs) if callable(result) else result
        return spy

    def plan(*_args, report=None, **_kwargs):
        if report is not None:
            report["kind"] = "speaker"
        return VF_PLAN[0], VF_PLAN[1], 0.71

    def render_final(source, final_path, *_args, **kwargs):
        if state["fail_broll"] and kwargs.get("broll_overlays"):
            return False, 1080, 1920
        Path(final_path).write_bytes(b"clip")
        return True, 1080, 1920

    def fast_copy(command, *args, **kwargs):
        calls.setdefault("stream_copy", []).append(_call([command], kwargs, norm))
        Path(command[-1]).write_bytes(b"clip")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video_utils, "get_config", lambda: SimpleNamespace(speaker_framing=True))
    monkeypatch.setattr(video_utils, "render_source_ranges_ffmpeg", record("render_source_ranges_ffmpeg", True))
    monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda _path: (1920, 1080))
    monkeypatch.setattr(video_utils, "ffprobe_duration", lambda _path: 18.4)
    monkeypatch.setattr(
        video_utils, "probe_video_geometry",
        lambda _path: SimpleNamespace(display_width=1920, display_height=1080, fps=30.0),
    )
    monkeypatch.setattr(
        video_utils, "build_clip_speaker_turns",
        record("build_clip_speaker_turns", ([{"speaker": "A", "start": 0.0, "end": 9.3}], [])),
    )
    monkeypatch.setattr(video_utils, "build_vertical_filter_plan", record("build_vertical_filter_plan", plan))
    monkeypatch.setattr(video_utils, "detect_burned_caption_band", lambda _path: state["band"])
    monkeypatch.setattr("src.media.reframing.caption_probe_plan", record("caption_probe_plan", VF_PLAN))
    monkeypatch.setattr(
        "src.media.source_captions.detect_rendered_caption_band",
        record("detect_rendered_caption_band", (0.66, 0.78)),
    )
    monkeypatch.setattr(
        video_utils, "build_assemblyai_ass_subtitles", record("build_assemblyai_ass_subtitles", True)
    )
    monkeypatch.setattr(video_utils, "render_reframed_clip_ffmpeg", record("render_reframed_clip_ffmpeg", render_final))
    monkeypatch.setattr(video_utils, "source_within_output_limits", lambda _path: True)
    monkeypatch.setattr(video_utils, "select_audio_stream_index", lambda _path: 1)
    monkeypatch.setattr(video_utils.subprocess, "run", fast_copy)

    footage = tmp_path / "stock.mp4"
    footage.write_bytes(b"stock")

    def run(*args, band=None, fail_broll=False, **kwargs):
        calls.clear()
        state.update(band=band, fail_broll=fail_broll)
        output = tmp_path / "out.mp4"
        output.unlink(missing_ok=True)
        if kwargs.get("broll_overlays") == "stock":
            kwargs["broll_overlays"] = [{"path": str(footage), "start": 2.0, "duration": 3.0, "offset": 0.0}]
        report = {}
        ok = video_utils.create_optimized_clip(source_video, *args[:2], output, *args[2:], layout_report=report, **kwargs)
        return {"ok": ok, "written": output.exists(), "layout_report": norm(report), "calls": calls}

    return run


OPTIMIZED_CASES = {
    # (start, end, add_subtitles, font_family, font_size, font_color, template, format, keep_ranges, hook), kwargs
    "vertical_captions_hook_broll_band": (
        (9.9, 28.9, True, None, None, None, "default", "vertical", RANGES, HOOK),
        {"extend_to_sentence": False, "broll_overlays": "stock", "band": (0.7, 0.82)},
    ),
    "vertical_offsets_point_labels": (
        (9.9, 28.9, True, "Poppins-ExtraBold", 40, "#FFEEDD", "hormozi", "vertical", RANGES, HOOK),
        {"extend_to_sentence": False, "output_offsets": OFFSETS,
         "point_labels": [{"text": "The loss", "start": 0.0}, {"text": "The fix", "start": 9.26}]},
    ),
    "vertical_broll_fallback": (
        (9.9, 28.9, True, None, None, None, "mrbeast", "vertical", RANGES, None),
        {"extend_to_sentence": False, "broll_overlays": "stock", "fail_broll": True},
    ),
    "vertical_no_subtitles": (
        (9.9, 28.9, False, None, None, None, "default", "vertical", RANGES, None),
        {"extend_to_sentence": False},
    ),
    "original_captions": (
        (9.9, 28.9, True, None, None, None, "podcast", "original", RANGES, HOOK),
        {"extend_to_sentence": False},
    ),
    "vertical_split": (
        (9.9, 28.9, True, None, None, None, "neon", "vertical_split", RANGES, HOOK),
        {"extend_to_sentence": False},
    ),
    "original_stream_copy": (
        (10.0, 19.0, False, None, None, None, "default", "original", None, None),
        {},
    ),
    "legacy_bounds_extend_to_sentence": (
        (10.0, 19.0, True, None, None, None, "tiktok", "vertical", None, None),
        {},
    ),
}


@pytest.mark.parametrize("case", sorted(OPTIMIZED_CASES))
def test_create_optimized_clip_calls(optimized, case):
    args, kwargs = OPTIMIZED_CASES[case]
    pin_json("create_optimized_clip", case, optimized(*copy.deepcopy(args), **copy.deepcopy(kwargs)))


# --------------------------------------------------------------------------- create_single_clip / batch / sidecar

HIGHLIGHT_SEGMENT = {
    "start_time": "00:10", "end_time": "00:19", "text": "So I lost $40,000 in one night.",
    "relevance_score": 0.91, "reasoning": "Strong hook", "virality_score": 82, "hook_score": 22,
    "engagement_score": 20, "value_score": 19, "shareability_score": 21, "hook_type": "story",
    "hook_title": HOOK,
}
RECAP_SEGMENT = {
    "start_time": "00:09", "end_time": "00:28", "text": "Recap text.", "relevance_score": 1.0,
    "reasoning": "", "planned": True, "source_ranges": [list(item) for item in RANGES],
    "junction_fades": [0.04], "max_seconds": 45.0, "hook_title": "The whole story",
    "workflow_role": "recap",
    "workflow_meta": {
        "planned": True, "ranges": [list(item) for item in RANGES], "junction_fades": [0.04], "max_seconds": 45.0,
        "points": [{"label": "The loss", "kind": "opener", "range_index": 0, "output_start": None},
                   {"label": "The fix", "kind": "closer", "range_index": 1, "output_start": None}],
    },
}
PLACEMENTS = [{"start": 2.0, "duration": 3.0, "pexels_id": 3943723, "query": "stack of cash", "footage_offset": 0.5}]


def _sidecar(clip_path: Path, norm) -> dict:
    path = clip_source_map_path(clip_path)
    return norm(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else {}


@pytest.fixture
def single_clip(source_video, tmp_path, monkeypatch, norm):
    seen: dict = {}
    report_values: dict = {}

    def fake_create(*args, **kwargs):
        seen.setdefault("create_optimized_clip", []).append(_call(args, {
            key: (sorted(value) if key == "layout_report" else value) for key, value in kwargs.items()
        }, norm))
        kwargs["layout_report"].update(copy.deepcopy(report_values))
        Path(args[3]).write_bytes(b"clip")
        return True

    async def fake_plan_broll(*args, **kwargs):
        seen.setdefault("plan_broll", []).append(_call(args, kwargs, norm))
        return copy.deepcopy(PLACEMENTS)

    async def fake_overlays(placements):
        seen.setdefault("broll_overlays_for", []).append(norm(placements))
        return [{"path": str(tmp_path / "stock.mp4"), "start": p["start"], "duration": p["duration"],
                 "offset": p["footage_offset"]} for p in placements]

    def fake_cap(path, short_side):
        seen.setdefault("cap_video_short_side", []).append(norm([path, short_side]))
        return True

    monkeypatch.setattr(video_service, "create_optimized_clip", fake_create)
    monkeypatch.setattr(VideoService, "plan_broll", staticmethod(fake_plan_broll))
    monkeypatch.setattr(video_service, "broll_overlays_for", fake_overlays)
    monkeypatch.setattr(video_service, "cap_video_short_side", fake_cap)

    async def run(segment, report=None, **kwargs):
        seen.clear()
        report_values.clear()
        report_values.update(report or {})
        output_dir = tmp_path / "clips"
        output_dir.mkdir(exist_ok=True)
        info = await VideoService.create_single_clip(source_video, copy.deepcopy(segment), 0, output_dir, **kwargs)
        assert info is not None
        sidecar = _sidecar(Path(info["path"]), norm)
        return {"calls": seen, "info": norm(info), "sidecar": sidecar, "sidecar_keys": sorted(sidecar)}

    return run


SINGLE_CLIP_CASES = {
    "highlight_plain": (HIGHLIGHT_SEGMENT, {}, {}),
    "highlight_styled_caption_y": (
        HIGHLIGHT_SEGMENT,
        {"caption_y": 0.71, "broll_applied": False, "vertical_layout": {"kind": "speaker"}},
        {"font_family": "Poppins-ExtraBold", "font_size": 40, "font_color": "#FFEEDD",
         "caption_template": "hormozi", "output_format": "vertical", "add_subtitles": True,
         "max_clip_seconds": 45.0},
    ),
    "highlight_broll_720p": (
        HIGHLIGHT_SEGMENT,
        {"caption_y": 0.7, "broll_applied": True},
        {"include_broll": True, "max_short_side": 720, "caption_template": "neon"},
    ),
    "highlight_broll_not_applied": (
        HIGHLIGHT_SEGMENT,
        {"broll_applied": False},
        {"include_broll": True},
    ),
    "recap_planned": (
        RECAP_SEGMENT,
        {"caption_y": 0.75, "broll_applied": False},
        {"max_clip_seconds": 30.0, "cleanup_settings": {"cut_long_pauses": False, "remove_filler_words": False}},
    ),
    "original_no_subtitles": (
        HIGHLIGHT_SEGMENT, {}, {"output_format": "original", "add_subtitles": False},
    ),
}


@pytest.mark.parametrize("case", sorted(SINGLE_CLIP_CASES))
async def test_create_single_clip_calls_and_sidecar(single_clip, case):
    segment, report, kwargs = SINGLE_CLIP_CASES[case]
    result = await single_clip(segment, report, **copy.deepcopy(kwargs))
    create_kwargs = result["calls"]["create_optimized_clip"][0]["kwargs"]
    assert "fx" not in create_kwargs and "caption_override" not in create_kwargs
    pin_json("create_single_clip", case, result)
    pin_json("sidecar_keys", f"create_single_clip|{case}", result["sidecar_keys"])


@pytest.fixture
def batch_clips(source_video, tmp_path, monkeypatch, norm):
    seen: list = []

    def fake_create(*args, **kwargs):
        seen.append(_call(args, {
            key: (sorted(value) if key == "layout_report" else value) for key, value in kwargs.items()
        }, norm))
        kwargs["layout_report"].update({
            "caption_y": 0.7, "broll_applied": bool(kwargs.get("broll_overlays")),
            "vertical_layout": {"kind": "speaker", "video_filter": VF_PLAN[0], "mode": "vf",
                                "width": 1920, "height": 1080, "duration": 18.4},
        })
        Path(args[3]).write_bytes(b"clip")
        return True

    monkeypatch.setattr(video_utils, "create_optimized_clip", fake_create)

    def run(segments, *args, **kwargs):
        seen.clear()
        clips = video_utils.create_clips_with_transitions(
            source_video, copy.deepcopy(segments), tmp_path / "batch", *args, **kwargs
        )
        sidecars = [_sidecar(Path(clip["path"]), norm) for clip in clips]
        return {
            "calls": seen,
            "clips": norm(clips),
            "sidecars": sidecars,
            "sidecar_keys": [sorted(sidecar) for sidecar in sidecars],
        }

    return run


def test_create_clips_with_transitions_calls_and_sidecar(batch_clips, tmp_path):
    with_broll = {**HIGHLIGHT_SEGMENT, "broll": copy.deepcopy(PLACEMENTS),
                  "broll_overlays": [{"path": str(tmp_path / "stock.mp4"), "start": 2.0, "duration": 3.0}]}
    plain = {**HIGHLIGHT_SEGMENT, "keep_ranges": [list(RANGES[1])], "broll": [], "broll_overlays": []}
    result = batch_clips(
        [with_broll, plain, RECAP_SEGMENT], None, None, None, "minimal", "vertical", True, None, max_clip_seconds=60.0
    )
    assert all("fx" not in call["kwargs"] and "fx_enabled" not in call["kwargs"] for call in result["calls"])
    pin_json("create_clips_with_transitions", "three_segments", result)
    pin_json("sidecar_keys", "create_clips_from_segments", result["sidecar_keys"])


@pytest.fixture
def video_clips(tmp_path, monkeypatch, norm):
    seen: dict = {}

    def fake_batch(*args, **kwargs):
        seen["create_clips_with_transitions"] = _call(args, kwargs, norm)
        return [{"clip_id": 1, "filename": "clip_1.mp4", "path": str(tmp_path / "clips" / "clip_1.mp4"),
                 "duration": 9.0, "keep_ranges": [list(RANGES[0])]}]

    async def fake_overlays(placements):
        seen.setdefault("broll_overlays_for", []).append(norm(placements))
        return [{"path": str(tmp_path / "stock.mp4"), "start": p["start"], "duration": p["duration"]}
                for p in placements]

    async def fake_plan_broll(*args, **kwargs):
        seen.setdefault("plan_broll", []).append(_call(args, kwargs, norm))
        return copy.deepcopy(PLACEMENTS)

    monkeypatch.setattr(video_service, "create_clips_with_transitions", fake_batch)
    monkeypatch.setattr(video_service, "get_config", lambda: SimpleNamespace(temp_dir=str(tmp_path)))
    monkeypatch.setattr(video_service, "broll_overlays_for", fake_overlays)
    monkeypatch.setattr(VideoService, "plan_broll", staticmethod(fake_plan_broll))
    monkeypatch.setattr(video_service, "cap_video_short_side", lambda *_args: True)
    monkeypatch.setattr(video_service, "rendered_clip_duration", lambda *_args: 8.96)
    return seen


@pytest.mark.parametrize("include_broll", [False, True], ids=["no_broll", "broll"])
async def test_create_video_clips_calls(video_clips, source_video, include_broll, norm):
    stored = {**HIGHLIGHT_SEGMENT, "keep_ranges": [list(RANGES[0])], "broll": copy.deepcopy(PLACEMENTS)}
    fresh = {**HIGHLIGHT_SEGMENT, "keep_ranges": [list(RANGES[1])]}
    clips = await VideoService.create_video_clips(
        source_video, [stored, fresh], None, 34, "#FFFFFF", "hormozi", "vertical", True, None,
        max_short_side=720, max_clip_seconds=90.0, include_broll=include_broll,
    )
    call = video_clips["create_clips_with_transitions"]
    assert set(call["kwargs"]) == {"max_clip_seconds"}
    pin_json("create_video_clips", "broll" if include_broll else "no_broll",
             {"calls": video_clips, "clips": norm(clips)})


async def test_regenerate_all_clips_calls_create_video_clips(isolated_clip_edits, monkeypatch, tmp_path, norm):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [RANGES[0]])
    save_clip_broll(clip_path, copy.deepcopy(PLACEMENTS))
    captured: dict = {}
    service = TaskService(db=None)
    service.task_repo.get_task_by_id = AsyncMock(return_value={
        "id": "task-1", "source_url": "upload://source.mp4", "source_type": "video_url", "user_id": None,
        "include_broll": True,
    })
    service.task_repo.update_task_clips = AsyncMock()
    service.clip_repo.get_clips_by_task = AsyncMock(return_value=[{
        "id": "clip-1", "task_id": "task-1", "file_path": str(clip_path), "start_time": "00:09",
        "end_time": "00:19", "duration": 9.3, "text": "So I lost", "relevance_score": 0.9, "reasoning": "Hook",
        "virality_score": 80, "hook_score": 20, "engagement_score": 20, "value_score": 20,
        "shareability_score": 20, "hook_type": "story", "hook_title": HOOK, "clip_order": 1,
        "workflow_role": None, "part_number": None, "workflow_meta": None,
    }])
    service.clip_repo.delete_clips_by_task = AsyncMock()
    service.clip_repo.create_clip = AsyncMock(return_value="new-clip")

    async def settings(_task_id):
        return {"output_format": "vertical", "add_subtitles": True, "clip_length": "auto",
                "cut_long_pauses": False, "pause_threshold_ms": 900, "remove_filler_words": False,
                "filtered_words": []}

    async def create_video_clips(*args, **kwargs):
        captured["create_video_clips"] = _call(args, kwargs, norm)
        return [{"clip_id": 1, "filename": "new.mp4", "path": str(tmp_path / "new.mp4"), "start_time": "00:09",
                 "end_time": "00:19", "duration": 9.3, "text": "So I lost"}]

    monkeypatch.setattr(service, "_load_task_source_settings", settings)
    monkeypatch.setattr(service, "_media_limits", AsyncMock(return_value=("1080", None)))
    service.video_service.create_video_clips = create_video_clips
    service.video_service.prepare_source = AsyncMock(side_effect=lambda path: path)
    monkeypatch.setattr(clip_service_module, "resolve_upload_for_edit", AsyncMock(return_value=tmp_path / "source.mp4"))
    (tmp_path / "source.mp4").write_bytes(b"source")

    await service.regenerate_all_clips_for_task("task-1", None, 34, "#FFFFFF", "podcast")

    create_kwargs = service.clip_repo.create_clip.await_args.kwargs
    assert "fx_meta" not in create_kwargs
    pin_json("regenerate_all_clips", "legacy_clip", {
        "create_video_clips": captured["create_video_clips"],
        "create_clip_kwargs": norm(create_kwargs),
    })


# --------------------------------------------------------------------------- process_task → create_single_clip

URL = "upload://source.mp4"


@pytest.mark.parametrize("row", [
    {"status": "queued"},
    {"status": "queued", "workflow": "highlights", "workflow_options": {}, "include_broll": True},
], ids=["pre_workflow_row", "highlights_broll"])
async def test_process_task_create_single_clip_kwargs(row, norm):
    service = build_task_service()
    service.task_repo.get_task_by_id = AsyncMock(return_value=row)
    service.task_repo.save_workflow_plan = AsyncMock()

    await service.process_task(
        task_id="task-1", url=URL, source_type="upload", font_family="Poppins-ExtraBold", font_size=40,
        font_color="#FFEEDD", caption_template="hormozi", clip_length="medium",
    )

    render = service.video_service.create_single_clip.await_args
    assert "fx" not in render.kwargs
    args = list(render.args)
    args[3] = "<CLIPS_DIR>"
    pin_json("process_task", f"create_single_clip|{'broll' if row.get('include_broll') else 'plain'}", {
        "args": norm(args),
        "kwargs": norm(render.kwargs),
        "create_clip_kwargs": sorted(service.clip_repo.create_clip.await_args.kwargs),
    })


# --------------------------------------------------------------------------- task repository SQL


class _Result:
    def __init__(self, row=None, scalar=None):
        self._row, self._scalar = row, scalar

    def fetchone(self):
        return self._row

    def scalar(self):
        return self._scalar


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _FakeDb:
    def __init__(self, row=None):
        self.statements: list = []
        self.info: dict = {}
        self.row = row

    def begin_nested(self):
        return _Nested()

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        return _Result(row=self.row, scalar=(params or {}).get("task_id"))

    async def commit(self):
        return None

    async def rollback(self):
        return None


@pytest.mark.parametrize("workflow", ["highlights", "series"])
async def test_create_task_insert_sql(workflow):
    db = _FakeDb()
    options = {"part_count": 3} if workflow == "series" else None
    task_id = await TaskRepository.create_task(
        db, "user-1", "source-1", status="queued", font_family="Poppins-ExtraBold", font_size=40,
        font_color="#FFFFFF", caption_template="hormozi", include_broll=True, processing_mode="fast",
        source_duration_seconds=600, workflow=workflow, workflow_options=options,
    )
    ((sql, params),) = db.statements
    assert params["task_id"] == task_id
    params["task_id"] = "<TASK_ID>"
    pin_text(f"create_task_insert_{workflow}", sql)
    pin_json("task_repository", f"create_task|{workflow}", {"sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
                                                            "params": params})


def _task_row(**missing):
    values = {
        "id": "task-1", "user_id": "user-1", "source_id": "source-1", "source_title": "Title",
        "source_type": "youtube", "source_url": "https://youtu.be/x", "status": "completed", "progress": 100,
        "progress_message": "Done", "generated_clips_ids": ["clip-1"], "font_family": "THEBOLDFONT",
        "font_size": 32, "font_color": "#FFFFFF", "caption_template": "default", "include_broll": False,
        "processing_mode": "fast", "cache_hit": False, "error_code": None, "stage_timings_json": None,
        "started_at": None, "completed_at": None, "completion_notification_sent_at": None,
        "source_duration_seconds": 600, "share_enabled": False, "workflow": None, "workflow_options": None,
        "workflow_plan": None, "created_at": "2026-11-13T00:00:00", "updated_at": "2026-11-13T00:00:00",
        "unrelated_column": "not returned",
    }
    for key in missing:
        values.pop(key)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("row", ["full", "pre_migration"])
async def test_get_task_by_id_keys(row):
    task_row = _task_row() if row == "full" else _task_row(
        workflow=1, workflow_options=1, workflow_plan=1, share_enabled=1, caption_template=1, include_broll=1,
    )
    db = _FakeDb(row=task_row)
    task = await TaskRepository.get_task_by_id(db, "task-1")
    (sql, params), = db.statements
    pin_text("get_task_by_id_select", sql)
    pin_json("task_repository", f"get_task_by_id|{row}", {"keys": sorted(task), "task": task, "params": params})


# --------------------------------------------------------------------------- settings route


async def test_settings_route_synchronous_response(isolated_clip_edits, monkeypatch, norm):
    from src.api.routes import tasks

    task_row = {"id": "task-1", "user_id": "user-1", "source_type": "youtube", "source_url": "https://youtu.be/x",
                "include_broll": True, "caption_template": "default"}
    task_with_clips = {
        "id": "task-1", "status": "completed", "caption_template": "neon", "font_family": None,
        "font_size": 30, "font_color": "#FFFFFF", "include_broll": True, "output_format": "vertical",
        "add_subtitles": True,
        "clips": [{"id": "clip-1", "filename": "clip_1.mp4", "duration": 9.3, "hook_title": HOOK}],
    }
    service = TaskService(db=None)
    service.task_repo.get_task_by_id = AsyncMock(return_value=task_row)
    service.task_repo.update_task_settings = AsyncMock()
    service.regenerate_all_clips_for_task = AsyncMock()
    service.get_task_with_clips = AsyncMock(return_value=task_with_clips)
    saved = AsyncMock()
    metadata = {"url": "https://youtu.be/x", "source_type": "youtube", "output_format": "vertical",
                "add_subtitles": True, "cut_long_pauses": True, "pause_threshold_ms": 700,
                "remove_filler_words": False, "filtered_words": [], "processing_job_id": "job-1"}
    monkeypatch.setattr(tasks, "TaskService", lambda _db: service)
    monkeypatch.setattr(tasks, "_require_task_owner", AsyncMock(return_value=task_row))
    monkeypatch.setattr(tasks, "_load_task_source_metadata", AsyncMock(return_value=metadata))
    monkeypatch.setattr(tasks, "_save_task_source_metadata", saved)

    async def database():
        yield None

    app = FastAPI()
    app.include_router(tasks.router)
    app.dependency_overrides[tasks.get_db] = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/tasks/task-1/settings", json={
            "caption_template": "neon", "font_size": 30, "font_color": "#FFFFFF", "apply_to_existing": True,
        })

    assert response.status_code == 200
    pin_json("settings_route", "apply_to_existing", {
        "status": response.status_code,
        "body": response.json(),
        "update_task_settings": _call(service.task_repo.update_task_settings.await_args.args[1:],
                                      service.task_repo.update_task_settings.await_args.kwargs, norm),
        "regenerate_all_clips_for_task": _call(service.regenerate_all_clips_for_task.await_args.args,
                                               service.regenerate_all_clips_for_task.await_args.kwargs, norm),
        "save_task_source_metadata": _call(saved.await_args.args, saved.await_args.kwargs, norm),
    })


# --------------------------------------------------------------------------- AI edit and editor document


def _fixture_document() -> EditDocument:
    return EditDocument.model_validate(json.loads((PINS / "editor_document.json").read_text(encoding="utf-8")))


def test_ai_edit_context_settings_schema_and_prompt():
    document = _fixture_document()
    pin_text("ai_edit_context", ai_edit_service.build_context(document, 12.0, 5.5, "Cut the um and make it punchier"))
    pin_text("ai_edit_context_no_playhead", ai_edit_service.build_context(document, 12.0, None, "Add B-roll"))
    pin_text("ai_edit_system_prompt", ai_edit_service.SYSTEM_PROMPT)
    pin_json("ai_edit", "SETTINGS", ai_edit_service.SETTINGS)
    pin_json("ai_edit", "SETTINGS.keys", list(ai_edit_service.SETTINGS))
    pin_json("ai_edit", "FONTS", list(ai_edit_service.FONTS))
    schema = ai_edit_service.EditPlan.model_json_schema()
    pin_text("ai_edit_op_schema", json.dumps(schema, indent=2, ensure_ascii=False) + "\n")


@pytest.mark.parametrize("document", ["fixture", "minimal"])
def test_editor_document_dump(document):
    if document == "fixture":
        model = _fixture_document()
    else:
        model = EditDocument.model_validate({"segments": [{"id": "s1", "start": 0.0, "end": 3.0}]})
    dumped = model.model_dump()
    assert "fx" not in dumped
    pin_json("editor_document", f"{document}|model_dump", dumped)
    pin_text(f"editor_document_{document}", json.dumps(model.model_dump(mode="json"), ensure_ascii=False) + "\n")
    pin_text(f"editor_document_{document}_json", model.model_dump_json() + "\n")
    # A dump validates back to the same document.
    assert EditDocument.model_validate(dumped).model_dump() == dumped


@pytest.mark.parametrize("aspect", ["vertical", "square"])
@pytest.mark.parametrize("template_id", LEGACY_TEMPLATES)
def test_studio_write_ass_sha256(tmp_path, monkeypatch, template_id, aspect):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    document = _fixture_document()
    document = document.model_copy(update={
        "captions": document.captions.model_copy(update={"template": template_id}),
        "framing": document.framing.model_copy(update={"aspect": aspect}),
    })
    width, height = editor_document.output_size(document, 1920, 1080)
    path = tmp_path / "captions.ass"
    editor_document.write_ass(path, document, width, height)
    text = path.read_text(encoding="utf-8")
    pin_json("studio_ass_sha256", f"{template_id}|{aspect}", {
        "size": [width, height],
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "dialogue_lines": sum(1 for line in text.splitlines() if line.startswith("Dialogue:")),
    })


@pytest.mark.parametrize("case", ["plain", "broll_square_muted", "silent_layout"])
def test_studio_render_document_argv(tmp_path, monkeypatch, norm, case):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    commands = []

    def run_render(command, directory, job_id, duration, **kwargs):
        commands.append({"argv": norm(command), "duration": duration, "kwargs": sorted(kwargs)})
        Path(command[-1]).write_bytes(b"export")

    def ffprobe(command, *args, **kwargs):
        return SimpleNamespace(stdout="" if case == "silent_layout" else "1\n", returncode=0)

    monkeypatch.setattr(editor_document, "run_render", run_render)
    monkeypatch.setattr(editor_document.subprocess, "run", ffprobe)
    monkeypatch.setattr("src.clip_editor._ffprobe_size", lambda _path: (1080, 1920))
    (tmp_path / "clean.mp4").write_bytes(b"clean")
    document = _fixture_document()
    broll_files = None
    if case == "broll_square_muted":
        document = document.model_copy(update={
            "framing": document.framing.model_copy(update={"aspect": "square", "fit": "contain"}),
            "muted": True,
        })
        footage = tmp_path / "footage.mp4"
        footage.write_bytes(b"stock")
        broll_files = {document.broll[0].footage.key: footage}
    elif case == "silent_layout":
        (tmp_path / "layout.mp4").write_bytes(b"layout")
        document = document.model_copy(update={
            "framing": document.framing.model_copy(update={"fit": "auto"}),
            "effects": document.effects.model_copy(update={"blur": 2}),
        })

    editor_document.render_document(tmp_path, "job-1", document, "reels" if case == "plain" else "tiktok", broll_files)

    assert (tmp_path / "export-job-1.mp4").exists()
    pin_json("studio_render_document", case, commands)
