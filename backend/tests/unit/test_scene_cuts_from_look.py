"""A scene change into busy footage: found by the change of the picture's look, where the spike tests miss it."""

import numpy as np

from src.media.reframing import LOOK_CUT_MIN_SHIFT, _scene_cuts_from_diffs, _scene_cuts_from_look

RATE = 3.0


def clip(levels, busy, seed=3):
    """32x18 thumbnails at the analysis rate: ``levels[i]`` is the picture's brightness, ``busy[i]`` how much
    of it changes from one sample to the next (handheld footage redraws most of the picture)."""
    rng = np.random.default_rng(seed)
    frames = []
    for level, noise in zip(levels, busy):
        frame = np.full((18, 32, 3), level, dtype=np.int16)
        frames.append(np.clip(frame + rng.integers(-noise, noise + 1, frame.shape), 0, 255).astype(np.int16))
    return [i / RATE for i in range(len(frames))], frames


def diffs_of(times, frames):
    return [(times[i], float(np.mean(np.abs(frames[i] - frames[i - 1])))) for i in range(1, len(frames))]


def test_a_cut_from_a_calm_dark_scene_to_busy_bright_footage_is_found():
    # A night street (dark, steady) for 8 s, then a lit room filmed handheld: every sample after the cut changes
    # almost as much as the cut itself, so the cut is no isolated spike and the clip's own threshold rises.
    times, frames = clip([25] * 24 + [95] * 24, [6] * 24 + [90] * 24)
    assert _scene_cuts_from_diffs(diffs_of(times, frames)) == []        # the spike test misses it
    assert _scene_cuts_from_look(times, frames) == [times[24]]


def test_busy_footage_that_keeps_its_look_has_no_cut():
    times, frames = clip([95] * 48, [90] * 48)
    assert _scene_cuts_from_look(times, frames) == []


def test_a_flash_that_returns_to_the_same_picture_is_not_a_cut():
    times, frames = clip([60] * 20 + [250] + [60] * 20, [6] * 41)
    assert _scene_cuts_from_look(times, frames) == []


def test_a_slow_change_of_light_is_not_a_cut():
    # Walking out of a doorway: the picture brightens over three seconds, never by much in one step.
    ramp = [40 + 6 * i for i in range(10)]
    times, frames = clip([40] * 15 + ramp + [100] * 15, [6] * 40)
    assert _scene_cuts_from_look(times, frames) == []


def test_a_cut_between_scenes_of_one_brightness_but_another_colour_is_found():
    times, frames = clip([90] * 40, [40] * 40)
    for frame in frames[20:]:
        frame[..., 0] = np.clip(frame[..., 0] + 2 * int(LOOK_CUT_MIN_SHIFT), 0, 255)   # one colour channel shifts
        frame[..., 2] = np.clip(frame[..., 2] - 2 * int(LOOK_CUT_MIN_SHIFT), 0, 255)
    assert _scene_cuts_from_look(times, frames) == [times[20]]


def test_too_short_a_clip_is_left_alone():
    times, frames = clip([25, 25, 95, 95], [6, 6, 90, 90])
    assert _scene_cuts_from_look(times, frames) == []


# ---------------------------------------------------------------------------
# Each find is checked against the frame-accurate cuts
# ---------------------------------------------------------------------------

def test_a_find_counts_only_on_a_frame_accurate_cut(monkeypatch, tmp_path):
    from src.media import interview_layout, reframing

    monkeypatch.setattr(interview_layout, "frame_changes", lambda path: ([0.0], np.zeros((1, 4))))
    monkeypatch.setattr(interview_layout, "precise_cuts", lambda times, frames, coarse: [8.02, 20.5])
    # 8.33 is the first sample after the cut at 8.02; 14.0 is an explosion inside one shot; 20.0 is before its cut.
    kept = reframing._look_cuts_on_real_cuts(tmp_path / "clip.mp4", [8.33, 14.0, 20.0, 20.67], 30.0)
    assert kept == [8.33, 20.67]


def test_footage_that_reads_as_a_cut_every_few_frames_confirms_nothing(monkeypatch, tmp_path):
    from src.media import interview_layout, reframing

    monkeypatch.setattr(interview_layout, "frame_changes", lambda path: ([0.0], np.zeros((1, 4))))
    monkeypatch.setattr(interview_layout, "precise_cuts", lambda times, frames, coarse: [i / 6 for i in range(120)])
    assert reframing._look_cuts_on_real_cuts(tmp_path / "clip.mp4", [8.33], 20.0) == []


def test_a_failed_frame_check_adds_no_cut(monkeypatch, tmp_path):
    from src.media import interview_layout, reframing

    def broken(path):
        raise RuntimeError("no frames")

    monkeypatch.setattr(interview_layout, "frame_changes", broken)
    assert reframing._look_cuts_on_real_cuts(tmp_path / "clip.mp4", [8.33], 20.0) == []


def test_a_cut_from_a_dark_street_to_busy_bright_footage_reaches_the_layout(tmp_path):
    import shutil
    import subprocess

    import pytest

    from src.media.reframing import analyze_vertical_clip

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    clip_path = tmp_path / "two_scenes.mp4"
    rng = np.random.default_rng(5)
    writer = subprocess.Popen([
        "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "320x180", "-r", "24", "-i", "-",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clip_path),
    ], stdin=subprocess.PIPE)
    # The busy scene: a camera sweeping over a patterned wall. Little changes from one frame to the next, but a
    # third of a second later the picture is another one, as in handheld footage.
    wall = np.kron(np.clip(95 + rng.integers(-90, 91, (9, 400)), 0, 255), np.ones((20, 20))).astype(np.uint8)
    for index in range(24 * 12):
        if index < 24 * 6:
            frame = np.full((180, 320), 25, dtype=np.uint8)                 # a dark, steady scene
        else:
            offset = (index - 24 * 6) * 7
            frame = wall[:, offset:offset + 320]
        writer.stdin.write(np.ascontiguousarray(frame).tobytes())
    writer.stdin.close()
    assert writer.wait() == 0
    cuts = analyze_vertical_clip(clip_path, analyze_context=False)[1]
    assert any(5.9 <= cut <= 6.4 for cut in cuts), cuts
