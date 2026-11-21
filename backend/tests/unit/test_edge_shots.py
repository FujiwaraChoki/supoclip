"""A clip never opens or closes on a few frames of a neighbouring shot.

Reviewed customer clips started with 4-8 frames of the previous scene (the clip starts a moment before
its first word, the source cuts to the speaker a moment later) and ended with 5-10 frames of the next one.
"""

import shutil
import subprocess

import numpy as np
import pytest

from src.media.edge_shots import FADE_DONE, dark_edge, dark_run, hold_stray_edge_shots, single_cut, stray_edge_frames

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")

FPS = 30


def _clip(path, shots):
    """A clip of solid-colour shots [(colour, seconds)] with a moving box in each, and a tone."""
    inputs, labels = [], []
    for index, (colour, seconds) in enumerate(shots):
        inputs += ["-f", "lavfi", "-i", f"color=c={colour}:s=320x180:r={FPS}:d={seconds}"]
        labels.append(f"[{index}:v]drawbox=x='20+40*t':y=60:w=40:h=40:color=white:t=fill[v{index}]")
    total = sum(seconds for _, seconds in shots)
    graph = ";".join(labels) + ";" + "".join(f"[v{i}]" for i in range(len(shots))) + f"concat=n={len(shots)}:v=1:a=0[v]"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, "-f", "lavfi", "-i", f"sine=frequency=440:duration={total}",
         "-filter_complex", graph, "-map", "[v]", "-map", f"{len(shots)}:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-g", "15", "-c:a", "aac", "-t", f"{total}", str(path)],
        check=True,
    )


def _frame_colour(path, index):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{index}),crop=20:20:290:150,scale=1:1",
         "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout
    r, g, b = raw[0], raw[1], raw[2]
    return "red" if r > 150 and g < 90 else "green" if g > 90 and r < 90 and b < 90 else "blue" if b > 150 else "other"


def _counts(path):
    def one(stream):
        return int(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", stream, "-count_packets", "-show_entries",
             "stream=nb_read_packets", "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True,
        ).stdout.strip().split("\n")[0].strip(","))
    return one("v:0"), one("a:0")


def test_stray_frames_at_both_ends_are_replaced_by_a_hold_of_the_clips_own_shot(tmp_path):
    clip = tmp_path / "source.mp4"
    # 5 frames of the previous shot, the clip's own shot, 7 frames of the next one.
    _clip(clip, [("red", 5 / FPS), ("blue", 4.0), ("green", 7 / FPS)])
    before = _counts(clip)
    assert stray_edge_frames(clip)[:2] == (5, 7)

    assert hold_stray_edge_shots(clip) == (5, 7)

    frames, audio = _counts(clip)
    assert (frames, audio) == before  # same length, same audio: captions and stored times still fit
    assert [_frame_colour(clip, i) for i in (0, 4, 5)] == ["blue", "blue", "blue"]
    assert [_frame_colour(clip, i) for i in (frames - 8, frames - 7, frames - 1)] == ["blue", "blue", "blue"]
    assert stray_edge_frames(clip)[:2] == (0, 0)


def test_a_clip_without_edge_cuts_is_left_untouched(tmp_path):
    clip = tmp_path / "source.mp4"
    _clip(clip, [("blue", 4.0)])
    stamp = clip.stat().st_mtime_ns
    assert hold_stray_edge_shots(clip) == (0, 0)
    assert clip.stat().st_mtime_ns == stamp


def test_a_real_opening_shot_and_a_montage_are_kept(tmp_path):
    longer = tmp_path / "longer.mp4"
    # A 0.7 s opening shot is a shot of the clip, not a sliver.
    _clip(longer, [("red", 0.7), ("blue", 4.0)])
    assert hold_stray_edge_shots(longer) == (0, 0)
    montage = tmp_path / "montage.mp4"
    # Flash cuts every 5 frames are the source's own edit.
    _clip(montage, [("red", 5 / FPS), ("green", 5 / FPS), ("red", 5 / FPS), ("blue", 4.0)])
    assert stray_edge_frames(montage)[0] == 0


def test_single_cut_ignores_a_flash_that_returns_to_the_same_picture():
    still = np.full((90, 160), 40, dtype=np.int16)
    flash = np.full((90, 160), 230, dtype=np.int16)
    other = np.full((90, 160), 140, dtype=np.int16)
    assert single_cut(np.stack([still] * 4 + [other] * 10)) == 4
    assert single_cut(np.stack([still] * 4 + [flash] + [still] * 9)) is None
    assert single_cut(np.stack([still] * 14)) is None


def test_single_cut_is_found_next_to_fast_motion_in_doubled_frames():
    """30 fps footage in a 60 fps file: every other step is zero, and the shot after the cut moves a lot."""
    rng = np.random.default_rng(7)
    before = [np.full((90, 160), 30, dtype=np.int16) + (i // 2) for i in range(9)]
    moving = []
    for i in range(38):
        if i % 2 == 0:
            frame = np.clip(rng.normal(120, 18, (90, 160)), 0, 255).astype(np.int16)
        moving.append(frame)
    assert single_cut(np.stack(before + moving)) == 9


def test_black_frames_at_an_edge_are_held_over(tmp_path):
    clip = tmp_path / "source.mp4"
    # 6 black frames (the gap after the previous scene), the clip's shot, 5 black frames.
    _clip(clip, [("black", 6 / FPS), ("blue", 4.0), ("black", 5 / FPS)])
    before = _counts(clip)
    assert hold_stray_edge_shots(clip) == (6, 5)
    frames, audio = _counts(clip)
    assert (frames, audio) == before
    assert [_frame_colour(clip, i) for i in (0, 5, frames - 1)] == ["blue", "blue", "blue"]


def test_dark_run_follows_a_fade_and_leaves_a_dark_scene_alone():
    def frames(levels):
        return np.stack([np.full((90, 160), level, dtype=np.int16) for level in levels])

    # Black, a fade up over four frames, then the picture at 120: held until half of that is reached.
    assert dark_run(frames([2, 4, 20, 45, 70, 100] + [120] * 18)) == 4
    # Straight from black to the picture.
    assert dark_run(frames([3] * 6 + [120] * 18)) == 6
    # A night scene is dark throughout: not a fade.
    assert dark_run(frames([10] * 24)) == 0
    # A clip that starts on its picture.
    assert dark_run(frames([120] * 24)) == 0


def test_a_long_stretch_of_black_is_part_of_the_clip(tmp_path):
    clip = tmp_path / "source.mp4"
    _clip(clip, [("black", 0.7), ("blue", 4.0)])
    assert hold_stray_edge_shots(clip) == (0, 0)


def test_a_short_fade_is_held_over_whole_so_the_held_frame_is_not_dim():
    def frames(levels):
        return np.stack([np.full((90, 160), level, dtype=np.int16) for level in levels])

    # 30 fps: three black frames, then a fade up over twelve (0.5 s in all), then the picture at 120.
    fade = [2, 2, 2] + [int(120 * step / 12) for step in range(1, 12)] + [120] * 28
    clip = frames(fade)
    assert dark_run(clip) == 8                                  # where half of the brightness is reached
    whole = dark_run(clip, FADE_DONE)
    assert fade[whole] >= 0.9 * 120 and whole == 13
    assert dark_edge(clip, limit=12, fade_limit=18) == 13       # the frame held is the picture, not a dim one
    # A fade of a second does not fit: the edge is left alone, since even its dark half is longer than a sliver.
    slow = frames([2] * 3 + [int(120 * step / 30) for step in range(1, 30)] + [120] * 10)
    assert dark_edge(slow, limit=12, fade_limit=18) == 0
    # Black, then a fade too long to hold over whole, whose dark half is short: the dim frame is still not black.
    medium = frames([2] * 2 + [int(120 * step / 20) for step in range(1, 20)] + [120] * 21)
    assert dark_run(medium, FADE_DONE) > 18 and 0 < dark_edge(medium, limit=12, fade_limit=18) <= 12


def test_a_fade_to_black_at_the_end_holds_the_last_bright_frame(tmp_path):
    clip = tmp_path / "fade.mp4"
    # Four seconds of picture that fade to black over the last 0.4 s.
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x2060c0:s=320x180:r={FPS}:d=4",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-vf", "fade=t=out:st=3.6:d=0.4",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip),
    ], check=True)
    before = _counts(clip)
    head, tail = hold_stray_edge_shots(clip)
    assert head == 0 and 10 <= tail <= 14
    assert _counts(clip) == before                              # same length, same audio
    last = _frame_colour(clip, before[0] - 1)
    assert last == "blue"                                       # the last frame is the picture, not a dimmed one


def test_black_in_a_dark_scene_and_a_dip_to_black_are_held_over():
    def frames(levels):
        return np.stack([np.full((90, 160), level, dtype=np.int16) for level in levels])

    # A night scene (30) that cuts to black for its last frames: seen from the end, black then the scene.
    assert dark_edge(frames([0] * 7 + [30] * 35), limit=12, fade_limit=18) == 7
    # The same scene with no black in it is left alone, and so is one that is black throughout.
    assert dark_edge(frames([30] * 42), limit=12, fade_limit=18) == 0
    assert dark_edge(frames([4] * 42), limit=12, fade_limit=18) == 0
    # A dip to black: the neighbouring scene (bright, another picture) fades through black into the clip's own.
    other = np.zeros((90, 160), dtype=np.int16)
    other[:, :80] = 200                                         # another picture: half white, half black
    dip = np.stack([other * 21 // 100, other * 12 // 100] + [np.full((90, 160), level, dtype=np.int16)
                                                              for level in [3, 1, 0, 2, 30] + [61] * 35])
    assert dark_edge(dip, limit=12, fade_limit=18) == 7        # held from the first frame of the clip's own picture
    # A strobe inside one scene returns to the same picture: nothing is held.
    strobe = frames([61, 61, 2, 2, 61] + [61] * 37)
    assert dark_edge(strobe, limit=12, fade_limit=18) == 0
