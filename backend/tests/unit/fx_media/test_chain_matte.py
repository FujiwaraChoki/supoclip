"""WP12a, AR-B1: the person matte rides in P1's alpha channel, and the base picture keeps its RGB everywhere (inside
and outside behind windows, where the matte is 0 and where it is 255). Also the analysis hand-off (§6.4)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from src.media import fx_chain
from tests.unit.fx_media.fx_chain_kit import (  # noqa: F401 - chain_deps is a fixture
    StubAnalysis,
    chain_deps,
    cue,
    frames_rgb,
    make_source,
    plan,
    requires_media,
    run_chain,
    write_half_matte,
)

W, H = 360, 640
FLAT = "0x3C78B4"
LOSSLESS = ["-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", "-r", "30"]


def behind_plan(duration: float):
    return plan(duration=duration, template="behind_head",
                cues=[cue("accent", 0.4, 1.2, variant="behind", text="HUGE", id="b0.behind")])


def _left_strip(frame: np.ndarray) -> np.ndarray:
    """Pixels in the matte's background half (alpha 0), below the top band where behind text is drawn."""
    return frame[int(H * 0.6): int(H * 0.8), 8: W // 6].reshape(-1, 3).astype(float)


def _assert_source_colour(frames: np.ndarray, indices, source: Path) -> None:
    """The strip matches the source picture decoded the same way (so decoder matrix choices cancel out)."""
    expected = _left_strip(frames_rgb(source, W, H)[0]).mean(axis=0)
    for n in indices:
        mean = _left_strip(frames[n]).mean(axis=0)
        assert np.abs(mean - expected).max() <= 3, (n, mean)


@requires_media
def test_studio_matte_keeps_rgb_inside_and_outside_behind_windows(tmp_path, chain_deps):
    source = make_source(tmp_path / "src.mp4", w=W, h=H, dur=2.0, video=FLAT)
    matte = write_half_matte(tmp_path / "matte.mp4", w=270, h=480, dur=2.0)
    request = fx_chain.FxRequest(plan=behind_plan(2.0), words=[], caption=None, matte_input=matte)
    result = run_chain(source, tmp_path / "out.mp4", request, w=W, h=H, encode_args=LOSSLESS)
    assert result.ok, result.error
    frames = frames_rgb(tmp_path / "out.mp4", W, H)
    assert len(frames) == 60
    _assert_source_colour(frames, [21, 27], source)            # inside the behind window: alpha 0 there, RGB kept
    _assert_source_colour(frames, [3, 50], source)             # outside: never black either
    # The matte reached P2 through the alpha channel: the behind text shows over the background half (alpha 0) and
    # is covered by the "person" half (alpha 255), which is redrawn over it as a cutout.
    band = frames[21][int(H * 0.1): int(H * 0.45)].astype(int)
    white = (band.min(axis=2) > 235)
    assert white[:, : W // 2 - 4].sum() > 200, "behind text missing over the background"
    assert white[:, W // 2 + 4:].sum() == 0, "behind text drawn over the person"
    assert chain_deps is None or chain_deps.calls == []   # a prebuilt matte means no analysis decode


@requires_media
def test_studio_matte_filter_is_applied_before_geometry(tmp_path, chain_deps):
    """matte_filter carries the studio's per-segment cuts: here a trim, so the matte follows the E timeline."""
    source = make_source(tmp_path / "src.mp4", w=W, h=H, dur=1.0, video=FLAT)
    matte = write_half_matte(tmp_path / "matte.mp4", w=270, h=480, dur=3.0)
    request = fx_chain.FxRequest(plan=behind_plan(1.0), words=[], caption=None, matte_input=matte,
                                 matte_filter="trim=start=1:end=2,setpts=PTS-STARTPTS")
    result = run_chain(source, tmp_path / "out.mp4", request, w=W, h=H, encode_args=LOSSLESS)
    assert result.ok, result.error
    _assert_source_colour(frames_rgb(tmp_path / "out.mp4", W, H), [15, 25], source)


@requires_media
def test_generation_matte_comes_from_one_analysis_call(tmp_path, chain_deps, monkeypatch):
    analysis = StubAnalysis(face={"version": 1, "space": "picture", "fps": 6,
                                  "samples": [[0.0, [[0.3, 0.2, 0.4, 0.25, 0.9]]]]})
    monkeypatch.setattr(fx_chain, "_analysis_module", lambda: analysis)
    source = make_source(tmp_path / "src.mp4", w=W, h=H, dur=2.0, video=FLAT)
    p = plan(duration=2.0, template="behind_head",
             cues=[cue("accent", 0.4, 1.2, variant="behind", text="HUGE", id="b0.behind"),
                   cue("zoom", 1.4, 1.8, mode="punch", scale=1.2, anchor="face")])
    result = run_chain(source, tmp_path / "out.mp4", fx_chain.FxRequest(plan=p, words=[], caption=None),
                       w=W, h=H, encode_args=LOSSLESS)
    assert result.ok, result.error
    assert len(analysis.calls) == 1                                  # one decode for face + matte (AR-M8)
    call = analysis.calls[0]
    assert call["face"] is True and call["matte_windows"] == [(0.4, 1.2)]
    assert call["matte_dest"] is not None and call["matte_dest"].suffix == ".mkv"
    assert result.face_track == analysis.face                        # handed back for the sidecar
    frames = frames_rgb(tmp_path / "out.mp4", W, H)
    _assert_source_colour(frames, [21, 50], source)


@requires_media
def test_known_face_track_skips_analysis(tmp_path, chain_deps, monkeypatch):
    analysis = StubAnalysis()
    monkeypatch.setattr(fx_chain, "_analysis_module", lambda: analysis)
    source = make_source(tmp_path / "src.mp4", w=W, h=H, dur=1.0)
    track = {"version": 1, "space": "picture", "fps": 6, "samples": [[0.0, [[0.3, 0.2, 0.4, 0.25, 0.9]]]]}
    p = plan(duration=1.0, cues=[cue("zoom", 0.2, 0.6, mode="punch", scale=1.2, anchor="face")])
    result = run_chain(source, tmp_path / "out.mp4", fx_chain.FxRequest(plan=p, words=[], caption=None,
                                                                        face_track=track))
    assert result.ok, result.error
    assert analysis.calls == [] and result.face_track == track


@requires_media
def test_no_person_means_no_matte_input(tmp_path, chain_deps, monkeypatch):
    """A window without a person draws the behind text in front: P1 gets no matte input at all."""
    analysis = StubAnalysis(person=False)
    monkeypatch.setattr(fx_chain, "_analysis_module", lambda: analysis)
    commands: list[list[str]] = []
    real = fx_chain.build_p1_command

    def spy(**kwargs):
        commands.append(real(**kwargs))
        return commands[-1]

    monkeypatch.setattr(fx_chain, "build_p1_command", spy)
    source = make_source(tmp_path / "src.mp4", w=W, h=H, dur=1.5, video=FLAT)
    result = run_chain(source, tmp_path / "out.mp4", fx_chain.FxRequest(plan=behind_plan(1.5), words=[],
                                                                        caption=None), w=W, h=H)
    assert result.ok, result.error
    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "alphamerge" not in graph and graph.endswith("format=rgba[fxp_v]")
    assert len(analysis.calls) == 1
