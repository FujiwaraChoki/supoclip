"""Composition quality: layout hysteresis, protected source text, readable fit.

Fixtures are reduced from the 2026-10-27 clip-quality audit (cooking case 05,
solo podcast case 10, tagesschau case 07, Fireship React case 06).
"""

from dataclasses import replace
from pathlib import Path
import shutil
import subprocess

import cv2
import numpy as np
import pytest

from src.media import reframing
from src.media.scene_context import (
    ContextAnalyzer,
    SceneEvidence,
    build_context_layout_plan,
    context_reason,
    stabilize_layout_plan,
)


def subject(time=0.0, **kwargs):
    return replace(SceneEvidence(time, face_area=0.05, face_count=1, face_span=0.2), **kwargs)


def shot(start, end, kind, reason, soft=False):
    return {"start": start, "end": end, "kind": kind, "reason": reason, "soft": soft}


def layout(plan):
    return [(round(p["start"], 3), round(p["end"], 3), p["kind"]) for p in plan]


# -- #283: layout hysteresis -------------------------------------------------

def test_cooking_clip_does_not_flash_a_crop_inside_the_wide_demonstration():
    # Per-shot decisions measured on the rebuilt case 05 clip 1: a 0.33s empty
    # opening shot and a 1.33s pan close-up inside 25s of wide kitchen shots.
    plan = [
        shot(0.0, 0.333, "fit", "no_reliable_close_subject"),
        shot(0.333, 4.333, "face", "confident_subject"),
        shot(4.333, 6.667, "fit", "subjects_spread_across_frame"),
        shot(6.667, 15.333, "fit", "interaction_exceeds_crop"),
        shot(15.333, 22.0, "fit", "subjects_spread_across_frame"),
        shot(22.0, 26.0, "fit", "no_reliable_close_subject"),
        shot(26.0, 29.667, "fit", "subjects_spread_across_frame"),
        shot(29.667, 31.0, "face", "confident_subject"),
        shot(31.0, 33.333, "fit", "subjects_spread_across_frame"),
        shot(33.333, 34.333, "fit", "no_reliable_close_subject"),
    ]
    stable = stabilize_layout_plan(plan)
    assert layout(stable) == [(0.0, 34.333, "fit")]
    assert stable[0]["reason"] == "stable_context"


def test_brief_wide_shot_of_the_same_speaker_keeps_the_face_crop():
    # Case 10 clip 2 at ~27s: a 2s wide shot of the lone host (face ~0.2% of
    # the frame) between close-ups was shown as a tiny letterboxed strip.
    close = [subject(i / 3) for i in range(0, 78)]
    wide = [subject(i / 3, face_area=0.0023, face_center=0.42) for i in range(78, 84)]
    after = [subject(i / 3) for i in range(84, 120)]
    samples = close + wide + after
    assert context_reason(wide[0], 0.316) == "no_reliable_close_subject"
    plan = build_context_layout_plan(samples, [26.0, 28.0], 40.0, 0.316)
    assert layout(plan) == [(0.0, 40.0, "face")]


def test_fast_close_and_wide_cutting_of_one_speaker_stays_cropped():
    # Two-second close-ups alternating with two-second wide shots of the same
    # host: widening every short crop would letterbox the entire clip.
    plan = [
        shot(0, 2, "face", "confident_subject"),
        shot(2, 4, "fit", "no_reliable_close_subject", soft=True),
        shot(4, 6, "face", "confident_subject"),
        shot(6, 8, "fit", "no_reliable_close_subject", soft=True),
        shot(8, 10, "face", "confident_subject"),
    ]
    assert layout(stabilize_layout_plan(plan)) == [(0, 10, "face")]


def test_brief_content_shot_between_close_ups_stays_full_frame():
    close = [subject(i / 3) for i in range(0, 78)]
    slide = [SceneEvidence(i / 3, text_rows=6) for i in range(78, 84)]
    after = [subject(i / 3) for i in range(84, 120)]
    plan = build_context_layout_plan(close + slide + after, [26.0, 28.0], 40.0, 0.316)
    assert layout(plan) == [(0.0, 40.0, "fit")]


def test_brief_crop_inside_a_wide_two_shot_keeps_speaker_framing_reason():
    plan = [
        shot(0, 10, "fit", "interaction_exceeds_crop"),
        shot(10, 11.5, "face", "confident_subject"),
        shot(11.5, 20, "fit", "interaction_exceeds_crop"),
    ]
    stable = stabilize_layout_plan(plan)
    assert stable == [shot(0, 20, "fit", "interaction_exceeds_crop")]


def test_repeated_context_changes_use_one_full_frame_layout():
    plan = [
        shot(0, 3, "face", "confident_subject"),
        shot(3, 6, "fit", "screen_content_outside_crop"),
        shot(6, 9, "face", "confident_subject"),
    ]
    assert layout(stabilize_layout_plan(plan)) == [(0, 9, "fit")]


# -- #290: text a face crop would cut ----------------------------------------

def _news_frame(with_text=True):
    """Light studio with a centred speaker, a placard and a lower third (case 07)."""
    frame = np.full((270, 480, 3), 225, np.uint8)
    cv2.ellipse(frame, (264, 70), (22, 30), 0, 0, 360, (150, 170, 200), -1)
    cv2.rectangle(frame, (205, 100), (325, 215), (60, 40, 30), -1)
    if with_text:
        cv2.rectangle(frame, (125, 205), (350, 255), (250, 250, 250), -1)
        cv2.putText(frame, "Lyceens, etudiants :", (180, 222), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (40, 40, 40), 1)
        cv2.putText(frame, "ce qui change pour vous", (180, 238), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (40, 40, 40), 1)
        cv2.rectangle(frame, (55, 228), (245, 262), (90, 60, 40), -1)
        cv2.putText(frame, "Edouard Geffray", (62, 243), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (240, 240, 240), 1)
        cv2.putText(frame, "Bildungsminister Frankreich", (62, 257), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (240, 240, 240), 1)
    return frame


FACE = [(242, 40, 44, 60)]


def test_placard_and_lower_third_cut_by_the_face_crop_keep_the_full_frame():
    sample = ContextAnalyzer().measure(_news_frame(), FACE, 0)
    assert sample.cut_text_lines >= 2
    assert context_reason(sample, 0.316) == "text_cut_by_crop"
    # The protected span covers the face and every text line.
    assert sample.protect_left < 0.15 and sample.protect_right > 0.65


def test_clean_close_up_keeps_its_face_crop():
    sample = ContextAnalyzer().measure(_news_frame(with_text=False), FACE, 0)
    assert sample.cut_text_lines == 0
    assert context_reason(sample, 0.316) is None


def test_dark_podcast_close_up_is_not_mistaken_for_text():
    # Cases 02/10: a large face on a black set with a microphone arm.
    frame = np.full((270, 480, 3), 12, np.uint8)
    cv2.ellipse(frame, (240, 95), (55, 75), 0, 0, 360, (120, 140, 180), -1)
    cv2.rectangle(frame, (150, 170), (330, 270), (25, 25, 25), -1)
    cv2.line(frame, (330, 60), (420, 200), (90, 90, 90), 4)
    sample = ContextAnalyzer().measure(frame, [(185, 20, 110, 150)], 0)
    assert sample.cut_text_lines == 0
    assert context_reason(sample, 0.316) is None


def test_text_lines_in_the_crop_only_do_not_widen():
    frame = np.full((270, 480, 3), 225, np.uint8)
    cv2.putText(frame, "inside crop", (200, 230), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (40, 40, 40), 1)
    cv2.putText(frame, "also inside", (200, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (40, 40, 40), 1)
    sample = ContextAnalyzer().measure(frame, FACE, 0)
    assert sample.cut_text_lines == 0


# -- #282: readable fit ------------------------------------------------------

def _react_samples():
    """Detail spans measured on case 06 clip 3 (code editor on a dark slide)."""
    spans = [((0.0, 0.7), (0.04, 0.93)), ((0.7, 2.0), (0.23, 0.46)),
             ((2.0, 17.7), (0.20, 0.84)), ((17.7, 18.7), (0.12, 0.65)),
             ((18.7, 24.0), (0.07, 1.00))]
    samples = []
    for i in range(72):
        t = i / 3
        span = next(s for (a, b), s in spans if a <= t < b)
        samples.append(SceneEvidence(t, detail_left=span[0], detail_right=span[1]))
    return samples


def test_code_on_a_plain_background_is_zoomed_without_cutting_it():
    samples = _react_samples()
    plan = [shot(0, 24, "fit", "no_reliable_close_subject")]
    windows = reframing.plan_fit_windows(plan, samples, 1920, 1080)
    zoomed = [w for w in windows if w[2]]
    assert len(zoomed) == 1
    start, end, crop_w, x = zoomed[0]
    assert crop_w == 1344 and end - start >= 5
    # Foreground grows from 608px to 868px tall on the 1080x1920 canvas.
    assert round(1080 * 1080 / crop_w) == 868
    for sample in samples:
        if start <= sample.time < end:
            assert x <= sample.detail_left * 1920 and sample.detail_right * 1920 <= x + crop_w
    # Windows tile the shot with no gaps.
    assert windows[0][0] == 0 and windows[-1][1] == 24
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:]))


def test_full_width_detail_keeps_the_whole_frame():
    samples = [SceneEvidence(i / 3, detail_left=0.02, detail_right=0.98) for i in range(30)]
    plan = [shot(0, 10, "fit", "distributed_action")]
    assert reframing.plan_fit_windows(plan, samples, 1920, 1080) == []


def test_brief_narrow_detail_does_not_pump_the_zoom():
    samples = [
        SceneEvidence(i / 3, detail_left=0.3, detail_right=0.6) if 9 <= i < 15
        else SceneEvidence(i / 3, detail_left=0.02, detail_right=0.98)
        for i in range(30)
    ]
    plan = [shot(0, 10, "fit", "no_reliable_close_subject")]
    assert reframing.plan_fit_windows(plan, samples, 1920, 1080) == []


def test_unmeasured_evidence_never_zooms():
    plan = [shot(0, 10, "fit", "no_reliable_close_subject")]
    assert reframing.plan_fit_windows(plan, [SceneEvidence(i / 3) for i in range(30)], 1920, 1080) == []


def test_cut_text_shot_frames_the_speaker_and_all_text():
    # Case 07 clip 4: placard, lower third and minister span ~0.08-0.69.
    samples = [SceneEvidence(i / 3, detail_left=0.03, detail_right=0.92,
                             protect_left=0.08, protect_right=0.69) for i in range(24)]
    plan = [shot(0, 8, "fit", "text_cut_by_crop")]
    [(start, end, crop_w, x)] = reframing.plan_fit_windows(plan, samples, 1920, 1080)
    assert (start, end) == (0, 8) and crop_w == 1344
    assert x <= 0.08 * 1920 and 0.69 * 1920 <= x + crop_w


def test_zoomed_fit_graph_has_one_branch_per_size():
    windows = [(0.0, 6.0, 1344, 332), (6.0, 12.0, None, 0), (12.0, 20.0, 1344, 300)]
    graph = reframing.build_full_frame_filter(fit_windows=windows)
    assert "split=3[bgsrc][ft0][ft1]" in graph
    assert "crop=1344:ih:x='if(lt(t\\,6.000)\\,332\\,300)'" in graph
    assert "between(t\\,6.000\\,12.000)" in graph
    assert graph.endswith("[vout]")
    compositor = reframing.build_vertical_compositor_filter(
        "crop=606:1080:0:0,scale=1080:1920", [(20.0, 30.0)], [(0.0, 20.0)], fit_windows=windows)
    assert "split=4[bgsrc][crsrc][ft0][ft1]" in compositor and compositor.endswith("[vout]")


def test_full_frame_fit_stays_on_past_the_probed_duration():
    # The stream can run a frame past ffprobe's duration; the full-frame graph
    # has no other foreground, so the last window must not switch off.
    windows = [(0.0, 6.0, 1344, 332), (6.0, 12.0, None, 0)]
    graph = reframing.build_full_frame_filter(fit_windows=windows)
    assert "between(t\\,6.000\\,1000000.000)" in graph
    assert "between(t\\,0.000\\,6.000)" in graph


def test_text_measurement_failure_keeps_the_face_crop(monkeypatch):
    def boom(_gray):
        raise RuntimeError("bad frame")

    monkeypatch.setattr(ContextAnalyzer, "_text_lines", staticmethod(boom))
    frame = np.full((270, 480, 3), 40, np.uint8)
    evidence = ContextAnalyzer().measure(frame, [(200, 60, 80, 100)], 0.0)
    assert evidence.cut_text_lines == 0
    assert (evidence.detail_left, evidence.detail_right) == (0.0, 1.0)


def test_fit_window_failure_falls_back_to_the_plain_fit(monkeypatch):
    def boom(*_args):
        raise RuntimeError("bad windows")

    monkeypatch.setattr(reframing, "plan_fit_windows", boom)
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _p: 10.0)
    monkeypatch.setattr(reframing, "analyze_vertical_clip",
                        lambda _p: ([], [], [], [SceneEvidence(t / 2) for t in range(20)]))
    described = reframing.describe_vertical_layout(Path("unused.mp4"), 1920, 1080)
    assert described["kind"] == "fit"
    assert described["video_filter"] == reframing.build_full_frame_filter()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_rendered_content_fit_is_larger_and_keeps_both_content_edges(tmp_path):
    source = tmp_path / "code.mp4"
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=0x15171c:s=320x180:r=10:d=2,drawbox=x=96:y=40:w=128:h=100:color=0x2a2e36:t=fill,"
        "drawbox=x=96:y=40:w=8:h=100:color=red:t=fill,drawbox=x=216:y=40:w=8:h=100:color=blue:t=fill",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    graph, mode, caption_y = reframing.build_vertical_filter_plan(Path(source), 320, 180)
    assert mode == "complex" and caption_y == 0.80
    assert "crop=192:ih" in graph
    decoded = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(source), "-filter_complex", graph,
        "-map", "[vout]", "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ], capture_output=True, timeout=60)
    assert decoded.returncode == 0, decoded.stderr
    frame = np.frombuffer(decoded.stdout, np.uint8).reshape(1920, 1080, 3)
    row = frame[960]
    red = np.flatnonzero((row[:, 0] > 180) & (row[:, 2] < 90))
    blue = np.flatnonzero((row[:, 2] > 180) & (row[:, 0] < 90))
    assert len(red) and len(blue)
    # Content spans ~720px instead of ~430px in a letterboxed fit.
    assert blue.max() - red.min() > 650
    # The content is taller too: a letterboxed fit puts the box top (source
    # y=40) at 656 + 40 * 3.375 = 791px; the zoom puts it higher.
    column = frame[:, red[len(red) // 2]]
    top = np.flatnonzero((column[:, 0] > 180) & (column[:, 2] < 90)).min()
    assert top < 720
