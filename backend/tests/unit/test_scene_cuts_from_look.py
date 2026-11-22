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
