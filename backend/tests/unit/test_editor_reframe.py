"""Opening a clip in the editor keeps split-screen and stacked layouts.

Clips with no such plan still start from the uncropped trim and a vertical
or original crop, which is what the editor did before.
"""

from contextlib import asynccontextmanager
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from src.editor_document import EditDocument, editor_dir, render_document
from src.services.editor_service import (
    apply_editor_layout,
    editor_preserved_layout,
    prepare_editor,
    read_state,
)


def word(start, end, speaker):
    return {"text": "hello", "start": start, "end": end, "speaker": speaker}


def _frames(boxes, count=12):
    return [(index / 3.0, [tuple(box) for box in boxes]) for index in range(count)]


@pytest.mark.parametrize("kind", ["crop", "speaker", "stage", "fit", "compositor"])
def test_plain_layouts_fall_back_to_the_editor_crop(monkeypatch, tmp_path, kind):
    clip = tmp_path / "clean.mp4"
    clip.write_bytes(b"")
    monkeypatch.setattr("src.media.reframing.ffprobe_video_size", lambda _path: (1920, 1080))
    monkeypatch.setattr(
        "src.media.reframing.describe_vertical_layout",
        lambda *_args, **_kwargs: {
            "kind": kind,
            "video_filter": "crop=1",
            "mode": "vf",
            "caption_y": None,
        },
    )

    assert editor_preserved_layout(clip, "vertical") is None


@pytest.mark.parametrize("kind", ["stack", "reaction", "overlap"])
def test_stacked_layouts_are_kept(monkeypatch, tmp_path, kind):
    clip = tmp_path / "clean.mp4"
    clip.write_bytes(b"")
    monkeypatch.setattr("src.media.reframing.ffprobe_video_size", lambda _path: (1920, 1080))
    monkeypatch.setattr(
        "src.media.reframing.describe_vertical_layout",
        lambda *_args, **_kwargs: {
            "kind": kind,
            "video_filter": f"{kind}-filter[vout]",
            "mode": "complex",
            "caption_y": 0.5,
        },
    )

    layout = editor_preserved_layout(clip, "vertical")
    assert layout["kind"] == kind
    assert layout["output_format"] == "vertical"
    assert layout["vertical_plan"] == (f"{kind}-filter[vout]", "complex")


@pytest.mark.parametrize("output_format", ["original", "vertical_pan"])
def test_non_composed_formats_do_not_plan_a_layout(monkeypatch, tmp_path, output_format):
    def forbidden(*_args, **_kwargs):
        raise AssertionError(output_format)

    monkeypatch.setattr("src.media.reframing.detect_speaker_reframe_plan", forbidden)
    monkeypatch.setattr("src.media.reframing.describe_vertical_layout", forbidden)
    monkeypatch.setattr("src.media.reframing.ffprobe_video_size", forbidden)

    assert editor_preserved_layout(tmp_path / "clean.mp4", output_format) is None


def test_split_screen_plan_is_kept_and_other_speaker_plans_are_not(monkeypatch, tmp_path):
    clip = tmp_path / "clean.mp4"
    clip.write_bytes(b"")
    plans = {
        "split": {"mode": "split", "regions": {"left": {}, "right": {}}},
        "pan": {"mode": "pan", "x_expression": "0"},
    }

    def detect(_path, _fmt):
        return plans["current"]

    monkeypatch.setattr("src.media.reframing.detect_speaker_reframe_plan", detect)
    plans["current"] = plans["split"]
    layout = editor_preserved_layout(clip, "vertical_split")
    assert layout["kind"] == "split"
    assert layout["speaker_plan"]["mode"] == "split"

    plans["current"] = plans["pan"]
    assert editor_preserved_layout(clip, "vertical_split") is None
    plans["current"] = None
    assert editor_preserved_layout(clip, "vertical_split") is None


def test_failed_layout_render_keeps_the_uncropped_source(monkeypatch, tmp_path):
    source = tmp_path / "clean.mp4"
    source.write_bytes(b"original-bytes")
    monkeypatch.setattr(
        "src.media.reframing.render_reframed_clip_ffmpeg",
        lambda *_args, **_kwargs: (False, 0, 0),
    )

    assert apply_editor_layout(source, {"output_format": "vertical", "vertical_plan": ("x", "vf")}) is False
    assert source.read_bytes() == b"original-bytes"


def test_describe_labels_a_call_stack(monkeypatch, tmp_path):
    from src.media.reframing import describe_vertical_layout

    samples = _frames([(160, 480, 220, 200), (1100, 500, 220, 200)])
    monkeypatch.setattr("src.media.reframing.ffprobe_duration", lambda _path: 8.0)
    monkeypatch.setattr(
        "src.media.reframing.analyze_vertical_clip",
        lambda *_args, **_kwargs: ([(t, 270.0, 0.03) for t, _ in samples], [], samples),
    )

    described = describe_vertical_layout(tmp_path / "clip.mp4", 1920, 1080)
    assert described["kind"] == "stack"
    assert described["mode"] == "complex"
    assert "overlay=0:1120" in described["video_filter"]


def test_describe_labels_a_reaction_stack(monkeypatch, tmp_path):
    from src.media import reaction_layout
    from src.media.reframing import describe_vertical_layout

    reactor = (536.0, 25.0, 32.0, 42.0)
    inset = (480.0, 0.0, 160.0, 120.0)
    faces = _frames([reactor, (200.0, 140.0, 90.0, 100.0)], count=18)
    monkeypatch.setattr("src.media.reframing.ffprobe_duration", lambda _path: 6.0)
    monkeypatch.setattr(
        "src.media.reframing.analyze_vertical_clip",
        lambda *_args, **_kwargs: ([], [1, 2, 3, 4, 5], faces),
    )
    monkeypatch.setattr(reaction_layout, "measure_reaction_inset", lambda *_args, **_kwargs: inset)

    described = describe_vertical_layout(tmp_path / "clip.mp4", 640, 360)
    assert described["kind"] == "reaction"
    assert described["mode"] == "complex"
    assert "vstack=inputs=2[vout]" in described["video_filter"]


def test_describe_labels_overlapping_speakers(monkeypatch, tmp_path):
    from src.media import speaker_framing
    from src.media.reframing import describe_vertical_layout

    duration = 2.4
    samples = [
        (i / 10, [(80.0, 30.0, 40.0, 40.0), (520.0, 30.0, 40.0, 40.0)])
        for i in range(round(duration * 10))
    ]
    monkeypatch.setattr(
        speaker_framing,
        "analyze_speaker_clip",
        lambda *_args, **_kwargs: {
            "face_samples": samples,
            "scene_cuts": [],
            "motion": np.zeros((len(samples), 36, 64)),
            "motion_fps": 10.0,
            "motion_cell": 10.0,
        },
    )
    monkeypatch.setattr(
        "src.media.reframing.analyze_vertical_clip",
        lambda *_args, **_kwargs: ([(t, 100.0, 0.02) for t, _ in samples], [], samples),
    )
    monkeypatch.setattr("src.media.reframing.ffprobe_duration", lambda _path: duration)

    described = describe_vertical_layout(
        tmp_path / "clip.mp4",
        640,
        360,
        [],
        [word(0, duration, "A"), word(0, duration, "B")],
    )
    assert described["kind"] == "overlap"
    assert described["mode"] == "complex"
    assert "vstack=inputs=2" in described["video_filter"]


def _overlap_samples(duration=2.4):
    return [
        (i / 10, [(80.0, 30.0, 40.0, 40.0), (520.0, 30.0, 40.0, 40.0)])
        for i in range(round(duration * 10))
    ]


def _patch_overlap_analysis(monkeypatch, duration, samples):
    from src.media import speaker_framing

    monkeypatch.setattr(
        speaker_framing,
        "analyze_speaker_clip",
        lambda *_args, **_kwargs: {
            "face_samples": samples,
            "scene_cuts": [],
            "motion": np.zeros((len(samples), 36, 64)),
            "motion_fps": 10.0,
            "motion_cell": 10.0,
        },
    )
    monkeypatch.setattr(
        "src.media.reframing.analyze_vertical_clip",
        lambda *_args, **_kwargs: ([(t, 100.0, 0.02) for t, _ in samples], [], samples),
    )
    monkeypatch.setattr("src.media.reframing.ffprobe_duration", lambda _path: duration)
    monkeypatch.setattr("src.media.reframing.ffprobe_video_size", lambda _path: (640, 360))


@pytest.mark.parametrize("enabled", [False, True])
def test_editor_overlap_follows_speaker_framing_flag(monkeypatch, tmp_path, enabled):
    """Opening the editor reproduces the layout clip generation would bake.

    SPEAKER_FRAMING=false omits diarized speakers during generation, so the
    editor must not invent an overlap layout from that same data.
    """
    from src.config import Config
    from src.media import speaker_framing
    from src.media.reframing import describe_vertical_layout as real_describe
    from src.services.editor_service import PRESERVED_LAYOUT_KINDS, bake_editor_layout

    config = Config()
    config.speaker_framing = enabled
    monkeypatch.setattr("src.config.get_config", lambda: config)

    duration = 2.4
    samples = _overlap_samples(duration)
    _patch_overlap_analysis(monkeypatch, duration, samples)
    fetched = []

    def turns(_source, _ranges):
        fetched.append(True)
        return ([], [word(0, duration, "A"), word(0, duration, "B")])

    monkeypatch.setattr(speaker_framing, "build_clip_speaker_turns", turns)
    supplied_words = []
    described = []

    def spy_describe(path, width, height, speaker_turns=None, speaker_words=None):
        supplied_words.append(list(speaker_words or []))
        result = real_describe(path, width, height, speaker_turns, speaker_words)
        described.append(result)
        return result

    monkeypatch.setattr("src.media.reframing.describe_vertical_layout", spy_describe)
    applied = []
    monkeypatch.setattr(
        "src.services.editor_service.apply_editor_layout",
        lambda _source, layout: applied.append(layout["kind"]) or True,
    )
    clean = tmp_path / "clean.mp4"
    source = tmp_path / "source.mp4"
    clean.write_bytes(b"clean")
    source.write_bytes(b"source")

    bake_editor_layout(clean, "vertical", source, [(0.0, duration)])

    overlap_words = [word(0, duration, "A"), word(0, duration, "B")]
    if enabled:
        assert fetched
        assert supplied_words == [overlap_words]
        assert described[0]["kind"] == "overlap"
        assert applied == ["overlap"]
    else:
        assert fetched == []
        assert supplied_words == [[]]
        assert described[0]["kind"] != "overlap"
        if described[0]["kind"] in PRESERVED_LAYOUT_KINDS:
            assert applied == [described[0]["kind"]]
        else:
            assert applied == []


def test_describe_labels_a_speaker_crop_as_not_stacked(monkeypatch, tmp_path):
    from src.media.reframing import describe_vertical_layout

    track = [(i / 3.0, 960.0, 0.02) for i in range(12)]
    monkeypatch.setattr("src.media.reframing.ffprobe_duration", lambda _path: 4.0)
    monkeypatch.setattr(
        "src.media.reframing.analyze_vertical_clip",
        lambda *_args, **_kwargs: (track, [], []),
    )
    monkeypatch.setattr(
        "src.media.speaker_framing.build_speaker_filter_plan",
        lambda *_args, **_kwargs: ("crop=606:1080:0:0,scale=1080:1920", "vf"),
    )

    described = describe_vertical_layout(
        tmp_path / "clip.mp4", 1920, 1080, [], [word(0, 1, "A")]
    )
    assert described["kind"] == "speaker"
    monkeypatch.setattr("src.media.reframing.ffprobe_video_size", lambda _path: (1920, 1080))
    assert editor_preserved_layout(tmp_path / "clip.mp4", "vertical", [], [word(0, 1, "A")]) is None


def _landscape(path: Path):
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "color=c=red:size=640x360:rate=30:duration=1,drawbox=x=320:y=0:w=320:h=360:color=blue:t=fill",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def _pixel(path: Path, x: int, y: int):
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", str(path),
    ]))
    width = int(probe["streams"][0]["width"])
    height = int(probe["streams"][0]["height"])
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])
    frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
    return frame[y, x], (width, height)


def _is_red(pixel) -> bool:
    return int(pixel[0]) > 180 and int(pixel[2]) < 80


def _is_blue(pixel) -> bool:
    return int(pixel[2]) > 180 and int(pixel[0]) < 80


def _prepare(monkeypatch, tmp_path, source: Path, output_format: str):
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    config.media_storage = "local"
    monkeypatch.setattr("src.config.get_config", lambda: config)
    clip = {
        "id": "clip",
        "task_id": "task",
        "filename": "clip.mp4",
        "file_path": str(tmp_path / "clip.mp4"),
        "text": "",
    }
    task = {
        "source_url": "upload://source.mp4",
        "source_type": "upload",
        "processing_mode": "fast",
    }
    calls = []

    def render(*args, **kwargs):
        calls.append(kwargs)
        shutil.copy(source, args[3])
        return True

    service = SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        cache_repo=SimpleNamespace(get_cache=AsyncMock(return_value=None)),
        video_service=SimpleNamespace(
            resolve_local_video_path=lambda _url: source,
            prepare_source=AsyncMock(side_effect=lambda path: path),
        ),
        config=SimpleNamespace(default_processing_mode="fast"),
        _build_cache_key=lambda *_args: "cache-key",
        _media_limits=AsyncMock(return_value=(None, None)),
        _get_clip_source_ranges=lambda _clip: [(0.0, 1.0)],
        _load_task_source_settings=AsyncMock(return_value={
            "output_format": output_format,
            "add_subtitles": False,
        }),
        _cleanup_source_video=lambda *_args, **_kwargs: None,
    )

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(close=AsyncMock())

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _db: service)
    monkeypatch.setattr("src.video_utils.create_optimized_clip", render)
    monkeypatch.setattr("src.video_utils.load_cached_transcript_data", lambda _path: None)
    return calls


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
@pytest.mark.parametrize("kind", ["split", "stack"])
async def test_prepare_keeps_split_and_stacked_layouts_in_preview_and_export(
    monkeypatch, tmp_path, kind
):
    source = tmp_path / "source.mp4"
    _landscape(source)
    calls = _prepare(
        monkeypatch,
        tmp_path,
        source,
        "vertical_split" if kind == "split" else "vertical",
    )
    if kind == "split":
        monkeypatch.setattr(
            "src.media.reframing.detect_speaker_reframe_plan",
            lambda _path, _fmt: {
                "mode": "split",
                "regions": {
                    "left": {"tile_w": 320, "tile_h": 360, "tile_x": 0, "tile_y": 0},
                    "right": {"tile_w": 320, "tile_h": 360, "tile_x": 320, "tile_y": 0},
                },
            },
        )
    else:
        monkeypatch.setattr(
            "src.media.reframing.describe_vertical_layout",
            lambda *_args, **_kwargs: {
                "kind": "stack",
                "mode": "complex",
                "caption_y": 0.5,
                "video_filter": (
                    "[0:v]split=2[l][r];"
                    "[l]crop=320:360:0:0,scale=1080:960,setsar=1[lv];"
                    "[r]crop=320:360:320:0,scale=1080:960,setsar=1[rv];"
                    "[lv][rv]vstack,setsar=1[vout]"
                ),
            },
        )
        monkeypatch.setattr(
            "src.media.speaker_framing.build_clip_speaker_turns",
            lambda *_args, **_kwargs: ([], []),
        )

    await prepare_editor({}, "task", "clip", "clip.mp4")

    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    state = read_state(directory)
    assert state["status"] == "ready", state
    assert calls and calls[0]["output_format"] == "original"
    framing = state["draft"]["document"]["framing"]
    assert framing["aspect"] == "vertical" and framing["zoom"] == 1 and framing["fit"] == "cover"
    preview, size = _pixel(directory / "clean.mp4", 540, 400)
    bottom, _ = _pixel(directory / "clean.mp4", 540, 1500)
    assert size == (1080, 1920)
    assert _is_red(preview) and _is_blue(bottom)
    assert state["width"] == 1080 and state["height"] == 1920

    document = EditDocument.model_validate(state["draft"]["document"])
    render_document(directory, "exportjob", document, "tiktok")
    exported, export_size = _pixel(directory / "export-exportjob.mp4", 540, 400)
    exported_bottom, _ = _pixel(directory / "export-exportjob.mp4", 540, 1500)
    assert export_size == (1080, 1920)
    assert _is_red(exported) and _is_blue(exported_bottom)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
@pytest.mark.parametrize("output_format,aspect", [("vertical", "vertical"), ("original", "original")])
async def test_prepare_without_a_layout_keeps_the_source_crop(
    monkeypatch, tmp_path, output_format, aspect
):
    source = tmp_path / "source.mp4"
    _landscape(source)
    calls = _prepare(monkeypatch, tmp_path, source, output_format)
    planned = []
    monkeypatch.setattr(
        "src.media.reframing.describe_vertical_layout",
        lambda *_args, **_kwargs: planned.append("describe") or {
            "kind": "crop",
            "video_filter": "crop=1",
            "mode": "vf",
            "caption_y": None,
        },
    )
    monkeypatch.setattr(
        "src.media.reframing.detect_speaker_reframe_plan",
        lambda *_args, **_kwargs: planned.append("split") or None,
    )
    monkeypatch.setattr(
        "src.media.reframing.render_reframed_clip_ffmpeg",
        lambda *_args, **_kwargs: planned.append("render") or (False, 0, 0),
    )

    await prepare_editor({}, "task", "clip", "clip.mp4")

    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    state = read_state(directory)
    assert state["status"] == "ready", state
    assert calls and calls[0]["output_format"] == "original"
    assert state["draft"]["document"]["framing"]["aspect"] == aspect
    _pixel_value, size = _pixel(directory / "clean.mp4", 20, 20)
    assert size == (640, 360)
    assert state["width"] == 640 and state["height"] == 360
    assert "render" not in planned
    if output_format == "original":
        assert planned == []
    else:
        assert planned == ["describe"]
