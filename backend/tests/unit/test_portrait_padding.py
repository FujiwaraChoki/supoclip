"""Padded portrait footage must not become a tiny picture inside another fit."""

from pathlib import Path
import shutil
import subprocess

import cv2
import numpy as np
import pytest

from src.media import reframing
from src.media.portrait_padding import plan_portrait_padding, portrait_padding_bounds
from src.media.scene_context import ContextAnalyzer
from src.services.editor_service import editor_preserved_layout


def padded_frame(portrait_width=152, black=False):
    rng = np.random.default_rng(41)
    portrait = rng.integers(30, 225, (270, portrait_width, 3), dtype=np.uint8)
    portrait[:, :12] = (20, 20, 240)
    portrait[:, -12:] = (240, 20, 20)
    background = cv2.resize(portrait, (480, round(270 * 480 / portrait_width)))
    y = (background.shape[0] - 270) // 2
    background = cv2.GaussianBlur(background[y:y + 270], (0, 0), 18)
    if black:
        background[:] = 0
    left = (480 - portrait_width) // 2
    background[:, left:left + portrait_width] = portrait
    return background, left, left + portrait_width


@pytest.mark.parametrize('portrait_width', [124, 152, 180, 210])
@pytest.mark.parametrize('black', [False, True])
def test_finds_picture_edges_without_cutting_original_portrait(portrait_width, black):
    frame, left, right = padded_frame(portrait_width, black)
    bounds = portrait_padding_bounds(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    assert bounds is not None
    assert left - 4 <= bounds[0] * 480 <= left
    assert right <= bounds[1] * 480 <= right + 4


def test_wide_scene_and_natural_background_blur_are_not_padding():
    rng = np.random.default_rng(8)
    wide = rng.integers(0, 256, (270, 480, 3), dtype=np.uint8)
    assert portrait_padding_bounds(cv2.cvtColor(wide, cv2.COLOR_BGR2GRAY)) is None
    # A sharp, central person against bokeh has no two full-height seams.
    blurred = cv2.GaussianBlur(wide, (0, 0), 18)
    mask = np.zeros((270, 480), np.uint8)
    cv2.ellipse(mask, (240, 135), (70, 115), 0, 0, 360, 255, -1)
    blurred[mask > 0] = wide[mask > 0]
    assert portrait_padding_bounds(cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY)) is None


def test_presenter_and_text_in_sidebars_prevent_removal(monkeypatch):
    frame, _, _ = padded_frame()
    analyzer = ContextAnalyzer()
    assert analyzer.measure(frame, [], 0).portrait_bounds is not None
    assert analyzer.measure(frame, [(20, 10, 40, 50)], 1 / 3).portrait_bounds is None
    monkeypatch.setattr(analyzer, '_text_lines', lambda _: [(12, 100, 130, 115)])
    assert analyzer.measure(frame, [], 2 / 3).portrait_bounds is None


def test_failed_text_analysis_keeps_the_whole_composition(monkeypatch):
    frame, _, _ = padded_frame()
    analyzer = ContextAnalyzer()

    def unavailable(_):
        raise RuntimeError('text analysis unavailable')

    monkeypatch.setattr(analyzer, '_text_lines', unavailable)
    assert analyzer.measure(frame, [], 0).portrait_bounds is None


def test_one_frame_and_missing_analysis_do_not_authorize_a_crop():
    assert plan_portrait_padding([], [], 4, 480) == []
    assert plan_portrait_padding([(0, (.34, .66))], [], 4, 480) == []
    samples = [(i / 3, (.34, .66) if i == 1 else None) for i in range(12)]
    assert plan_portrait_padding(samples, [], 4, 480) == []


def test_padding_crop_stops_at_wide_shots_and_restarts_with_new_geometry():
    samples = [(i / 3, (.34, .66) if i < 6 else
                (.30, .70) if i >= 12 else None) for i in range(18)]
    windows = plan_portrait_padding(samples, [2, 4], 6, 480)
    assert [(a, b) for a, b, _, _ in windows] == [(0, 2), (4, 6)]
    assert windows[0][2:] != windows[1][2:]


def test_cut_and_decoder_gap_do_not_extend_a_confirmed_crop():
    samples = [(0, (.34, .66)), (1 / 3, (.34, .66)), (2 / 3, (.34, .66)),
               (2, None), (7 / 3, None)]
    assert plan_portrait_padding(samples, [.85], 3, 480)[0][1] == .85


def test_same_padding_through_short_montage_cuts_does_not_flash():
    samples = [(i / 3, (.34, .66)) for i in range(18)]
    windows = plan_portrait_padding(samples, [1, 4 / 3, 3, 10 / 3], 6, 480)
    assert [(a, b) for a, b, _, _ in windows] == [(0, 6)]


def test_editor_keeps_the_portrait_composition_and_uncropped_source(monkeypatch, tmp_path):
    monkeypatch.setattr(reframing, 'ffprobe_video_size', lambda _: (1920, 1080))
    monkeypatch.setattr(reframing, 'describe_vertical_layout', lambda *_: {
        'kind': 'embedded_portrait', 'video_filter': 'portrait[vout]',
        'mode': 'complex', 'caption_y': .8,
    })
    assert editor_preserved_layout(tmp_path / 'clean.mp4', 'vertical') is None
    layout = editor_preserved_layout(tmp_path / 'clean.mp4', 'vertical', include_content=True)
    assert layout['kind'] == 'embedded_portrait'
    assert layout['vertical_plan'] == ('portrait[vout]', 'complex')
    assert layout['landscape_recommended'] is False


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_real_render_enlarges_portrait_and_preserves_wide_edges_audio_and_timing(tmp_path, monkeypatch):
    portrait, _, _ = padded_frame()
    # Red/blue edge landmarks must survive the intervening real wide shot.
    wide = np.full_like(portrait, 80)
    wide[:, :70] = (20, 20, 240)
    wide[:, -70:] = (240, 20, 20)
    wide[::12] = 240  # genuine picture detail continues to both source edges
    frames = [portrait] * 12 + [wide] * 12 + [portrait] * 12
    source = tmp_path / 'padded-and-wide.mp4'
    encoded = subprocess.run([
        'ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
        '-s', '480x270', '-r', '6', '-i', '-', '-f', 'lavfi', '-i',
        'sine=frequency=440:duration=6', '-c:v', 'libx264', '-crf', '18',
        '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(source),
    ], input=b''.join(f.tobytes() for f in frames), capture_output=True, timeout=30)
    assert encoded.returncode == 0, encoded.stderr.decode()
    monkeypatch.setattr(reframing, '_open_face_detectors', lambda: (None, None))
    monkeypatch.setattr(reframing, '_detect_face_boxes', lambda *_: [])
    plan = reframing.describe_vertical_layout(source, 480, 270)
    assert plan['kind'] == 'embedded_portrait'
    output = tmp_path / 'vertical.mp4'
    ok, w, h = reframing.render_reframed_clip_ffmpeg(
        source, output, 'vertical', vertical_plan=(plan['video_filter'], plan['mode']))
    assert ok and (w, h) == (1080, 1920)
    assert reframing.ffprobe_has_audio(output)
    assert abs(reframing.ffprobe_duration(output) - 6) < .15
    decoded = subprocess.run([
        'ffmpeg', '-v', 'error', '-i', str(output), '-vf', 'fps=2,scale=108:192',
        '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-',
    ], capture_output=True, timeout=30)
    assert decoded.returncode == 0, decoded.stderr.decode()
    previews = np.frombuffer(decoded.stdout, np.uint8).reshape(-1, 192, 108, 3)
    assert len(previews) == 12
    # Both portrait edges now reach almost the output edges, before and after
    # the wide shot. A double fit would leave these points blurred background.
    for index in (1, 3, 9, 11):
        assert previews[index, 96, 4, 0] > 180
        assert previews[index, 96, 103, 2] > 180
    for index in (4, 5, 6, 7):
        assert previews[index, 96, 4, 0] > 180
        assert previews[index, 96, 103, 2] > 180
