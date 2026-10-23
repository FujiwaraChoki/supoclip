from pathlib import Path
from unittest.mock import patch

from src.media import reframing
from src.media.common import UPSCALE_SHARPEN_FILTER
from src.media.source_limits import VideoGeometry


class _Result:
    returncode = 0
    stdout = ""
    stderr = ""


def test_kenburns_fragment_sharpens_after_zoompan():
    fragment = reframing.kenburns_zoom_fragment(10.0)

    assert fragment is not None
    assert fragment.endswith(f",{UPSCALE_SHARPEN_FILTER},setsar=1")
    assert fragment.index("zoompan") < fragment.index(UPSCALE_SHARPEN_FILTER)


def test_static_vertical_crop_sharpens_after_upscale():
    with (
        patch.object(reframing, "ffprobe_duration", return_value=2.0),
        patch.object(reframing, "analyze_vertical_clip", return_value=(
            [(i / 3, 960.0, 0.04) for i in range(6)], [],
        )),
        patch.object(reframing, "detect_optimal_crop_region", return_value=(656, 0, 608, 1080)),
    ):
        video_filter, mode, caption_y = reframing.build_vertical_filter_plan(
            Path("in.mp4"), 1920, 1080
        )

    assert caption_y is None
    assert mode == "vf"
    assert video_filter.endswith(
        f"scale=1080:1920:flags=lanczos,{UPSCALE_SHARPEN_FILTER},setsar=1"
    )


def test_compositor_sharpens_face_crop_only():
    crop_chain = reframing.VERTICAL_SCALE_CHAIN
    graph = reframing.build_vertical_compositor_filter(
        f"crop=608:1080:0:0,{crop_chain}", [(0.0, 2.0)], [(2.0, 4.0)]
    )
    layers = {part.split("]", 1)[0].lstrip("["): part for part in graph.split(";")}

    assert UPSCALE_SHARPEN_FILTER in layers["crsrc"]
    assert UPSCALE_SHARPEN_FILTER not in layers["bgsrc"]
    # Landscape content is downscaled into the fit layer, so it stays unsharpened.
    assert UPSCALE_SHARPEN_FILTER not in layers["ftsrc"]


def test_original_format_is_not_sharpened(tmp_path):
    commands = []

    def fake_run(command, timeout=900):
        commands.append(command)
        return _Result()

    with (
        patch.object(reframing, "ffprobe_video_size", return_value=(1920, 1080)),
        patch.object(reframing, "ffprobe_has_audio", return_value=True),
        patch.object(reframing, "run_ffmpeg_command", side_effect=fake_run),
    ):
        ok, _, _ = reframing.render_reframed_clip_ffmpeg(
            tmp_path / "in.mp4",
            tmp_path / "out.mp4",
            "original",
            subtitle_ass_path=tmp_path / "captions.ass",
        )

    assert ok is True
    assert UPSCALE_SHARPEN_FILTER not in " ".join(commands[0])


def test_original_format_reencodes_sources_above_1080p(tmp_path):
    commands = []

    def fake_run(command, timeout=900):
        commands.append(command)
        return _Result()

    with (
        patch.object(reframing, "ffprobe_video_size", return_value=(3840, 2160)),
        patch.object(reframing, "ffprobe_has_audio", return_value=True),
        patch.object(reframing, "probe_video_geometry", return_value=VideoGeometry(3840, 2160, 120, 0)),
        patch.object(reframing, "run_ffmpeg_command", side_effect=fake_run),
    ):
        ok, width, height = reframing.render_reframed_clip_ffmpeg(
            tmp_path / "in.mp4", tmp_path / "out.mp4", "original"
        )

    assert ok is True
    assert (width, height) == (1920, 1080)
    video_filter = commands[0][commands[0].index("-vf") + 1]
    assert "scale=1920:1080" in video_filter
    assert "fps=60" in video_filter


def test_original_format_copies_a_source_already_within_the_cap(tmp_path):
    source = tmp_path / "in.mp4"
    source.write_bytes(b"source-bytes")
    output = tmp_path / "out.mp4"

    with (
        patch.object(reframing, "ffprobe_video_size", return_value=(1920, 1080)),
        patch.object(reframing, "ffprobe_has_audio", return_value=False),
        patch.object(reframing, "probe_video_geometry", return_value=VideoGeometry(1920, 1080, 30, 0)),
        patch.object(reframing, "run_ffmpeg_command") as run,
    ):
        ok, width, height = reframing.render_reframed_clip_ffmpeg(source, output, "original")

    assert ok is True
    assert (width, height) == (1920, 1080)
    assert output.read_bytes() == b"source-bytes"
    run.assert_not_called()


def test_split_layout_sharpens_both_tiles(tmp_path):
    commands = []
    region = {"tile_w": 800, "tile_h": 720, "tile_x": 0, "tile_y": 0}
    plan = {"mode": "split", "regions": {"left": region, "right": region}}

    def fake_run(command, timeout=900):
        commands.append(command)
        return _Result()

    with (
        patch.object(reframing, "ffprobe_video_size", return_value=(1920, 1080)),
        patch.object(reframing, "ffprobe_has_audio", return_value=True),
        patch.object(reframing, "detect_speaker_reframe_plan", return_value=plan),
        patch.object(reframing, "run_ffmpeg_command", side_effect=fake_run),
    ):
        ok, _, _ = reframing.render_reframed_clip_ffmpeg(
            tmp_path / "in.mp4", tmp_path / "out.mp4", "vertical_split"
        )

    assert ok is True
    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert graph.count(UPSCALE_SHARPEN_FILTER) == 2
