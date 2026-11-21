"""WP12a, AR-B2: FX renders convert with explicit colour matrices and tag the output bt709/tv, matching the legacy
encoder's result within ±2 code values (runs on whichever ffmpeg is installed: 6.1 in the fx-media-ffmpeg61 CI job,
7.1 in the image)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.media import fx_chain
from src.media.ffmpeg import build_final_video_encode_args
from tests.unit.fx_media.fx_chain_kit import (  # noqa: F401 - chain_deps is a fixture
    chain_deps,
    cue,
    frames_yuv_means,
    head_for,
    make_source,
    plan,
    probe,
    requires_ffmpeg,
    requires_media,
    run_chain,
)

W, H = 360, 640
FLAT = "0x3C78B4"   # a mid blue: every channel away from the clip points


def legacy_render(source: Path, out: Path) -> Path:
    """Today's single-pass render shape (reframing.composited without B-roll): head → final encode, no conversion."""
    head, label = head_for(W, H)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-filter_complex", head, "-map", label, "-map", "0:a?",
         *build_final_video_encode_args(), "-c:a", "aac", str(out)],
        check=True, capture_output=True,
    )
    return out


def _tags(path: Path) -> tuple[str, str, str, str]:
    video = probe(path)["video"]
    return (video.get("color_space"), video.get("color_primaries"), video.get("color_transfer"),
            video.get("color_range"))


def _compare(fx: Path, legacy: Path, frames: slice = slice(5, 25)) -> np.ndarray:
    a = frames_yuv_means(fx)[frames]
    b = frames_yuv_means(legacy)[frames]
    return np.abs(a - b).max(axis=0)


@requires_media
@pytest.mark.parametrize("tagged", [True, False], ids=["bt709-tagged", "untagged"])
def test_flat_colour_matches_legacy_within_two_code_values(tmp_path, chain_deps, tagged):
    source = make_source(tmp_path / "src.mp4", w=W, h=H, dur=1.0, video=FLAT, color_tagged=tagged)
    legacy = legacy_render(source, tmp_path / "legacy.mp4")
    result = run_chain(source, tmp_path / "fx.mp4", fx_chain.FxRequest(plan=plan(duration=1.0), words=[], caption=None),
                       w=W, h=H, encode_args=None)
    assert result.ok, result.error
    assert _compare(tmp_path / "fx.mp4", legacy).max() <= 2
    assert _tags(tmp_path / "fx.mp4") == ("bt709", "bt709", "bt709", "tv")


@requires_media
def test_frames_drawn_by_p2_keep_the_colour(tmp_path, chain_deps):
    """A progress bar makes P2 draw every frame (canvas round trip, not pass-through): the picture keeps its colour."""
    source = make_source(tmp_path / "src.mp4", w=W, h=H, dur=1.0, video=FLAT)
    legacy = legacy_render(source, tmp_path / "legacy.mp4")
    result = run_chain(source, tmp_path / "fx.mp4",
                       fx_chain.FxRequest(plan=plan(duration=1.0, progress_bar=True), words=[], caption=None),
                       w=W, h=H, encode_args=None)
    assert result.ok, result.error
    # Centre crop for Y; U/V means include the bar, so compare Y only here and the chroma in a centre crop below.
    fx_frames = _raw_yuv(tmp_path / "fx.mp4")
    legacy_frames = _raw_yuv(legacy)
    for plane in range(3):
        assert abs(fx_frames[plane] - legacy_frames[plane]) <= 2, plane
    assert _tags(tmp_path / "fx.mp4") == ("bt709", "bt709", "bt709", "tv")


@requires_media
def test_safe_mode_colour_and_tags(tmp_path, chain_deps):
    source = make_source(tmp_path / "src.mp4", w=W, h=H, dur=1.0, video=FLAT)
    legacy = legacy_render(source, tmp_path / "legacy.mp4")
    result = run_chain(source, tmp_path / "fx.mp4",
                       fx_chain.FxRequest(plan=plan(duration=1.0, cues=[cue("sfx", 0.5, sound="pop")]), words=[],
                                          caption=None),
                       w=W, h=H, encode_args=None, mode="safe")
    assert result.ok, result.error
    assert _compare(tmp_path / "fx.mp4", legacy).max() <= 2
    assert _tags(tmp_path / "fx.mp4") == ("bt709", "bt709", "bt709", "tv")


@requires_ffmpeg
def test_bt601_source_is_converted_with_its_own_matrix(tmp_path):
    """A BT.601-tagged source is read with the 601 matrix (not mis-read as 709), so its RGB survives into P1."""
    source = tmp_path / "src601.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={FLAT}:s={W}x{H}:r=30:d=0.5",
         "-vf", "scale=out_color_matrix=bt601:out_range=tv,format=yuv420p", "-c:v", "libx264", "-crf", "8",
         "-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m", "-color_range", "tv",
         str(source)],
        check=True, capture_output=True,
    )
    assert fx_chain.probe_color(source) == ("smpte170m", "tv")
    head, label = head_for(W, H)
    cmd = fx_chain.build_p1_command(
        input_path=source, head=head, head_label=label, out_w=W, out_h=H, broll_overlays=[], broll_inputs=[], tail="",
        color=fx_chain.probe_color(source), reaction_args=[], reaction_graph="", reaction_label=None, matte_input=None,
        matte_filter=None, in_frames=5,
    )
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    rgba = np.frombuffer(raw, dtype=np.uint8).reshape(-1, H, W, 4)
    centre = rgba[:, H // 4: 3 * H // 4, W // 4: 3 * W // 4, :3].reshape(-1, 3).mean(axis=0)
    expected = np.array([0x3C, 0x78, 0xB4], dtype=float)
    assert np.abs(centre - expected).max() <= 3, centre
    assert (rgba[..., 3] == 255).all()


def _raw_yuv(path: Path) -> tuple[float, float, float]:
    """Mean Y/U/V over a centre crop (away from the bottom progress bar), all frames."""
    info = probe(path)["video"]
    w, h = int(info["width"]), int(info["height"])
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "yuv444p", "-"],
        check=True, capture_output=True,
    ).stdout
    planes = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3, h, w)[:, :, h // 4: h // 2, w // 4: 3 * w // 4]
    return tuple(float(planes[:, i].mean()) for i in range(3))
