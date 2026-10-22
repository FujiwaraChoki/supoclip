import json
import re
import shutil
import subprocess
from unittest.mock import patch

import numpy as np
import pytest

from src.media import reaction_layout, reframing
from src.media.captions import build_hook_title_ass


REACTOR = (536., 25., 32., 42.)
INSET = (480., 0., 160., 120.)
MAIN = (200., 140., 90., 100.)


def samples(faces, count=18):
    return [(i / 3, list(faces)) for i in range(count)]


def test_reaction_anchor_survives_source_cuts_and_three_faces():
    frames = samples([REACTOR, MAIN, (70., 170., 40., 50.)])
    assert reaction_layout.find_reactor_face(640, 360, frames) == REACTOR


def test_regular_podcast_and_moving_corner_face_are_not_reaction_insets():
    assert reaction_layout.find_reactor_face(640, 360, samples([MAIN, (420., 140., 90., 100.)])) is None
    frames = [(i / 3, [(490. + i * 5, 25., 32., 42.), MAIN]) for i in range(18)]
    assert reaction_layout.find_reactor_face(640, 360, frames) is None


def test_reaction_requires_a_measurable_video_boundary(tmp_path):
    with patch.object(reaction_layout, 'measure_reaction_inset', return_value=None):
        assert reaction_layout.choose_reaction_layout(tmp_path / 'ordinary.mp4', 640, 360, 6, samples([REACTOR, MAIN])) is None


def test_hidden_source_face_does_not_switch_to_the_reactor_or_flicker():
    frames = samples([REACTOR, MAIN])
    frames[6:9] = [(i / 3, [REACTOR]) for i in range(6, 9)]
    plan = reaction_layout.build_reaction_layout(640, 360, 6, REACTOR, INSET, frames)
    assert len(plan['source_runs']) == 1
    assert plan['source_runs'][0][2] == 'close'
    assert all(face == MAIN for face in plan['source_runs'][0][3])
    assert plan['caption_y'] == pytest.approx(1 / 3)


def test_source_face_under_the_inset_is_cropped_below_it():
    plan = reaction_layout.build_reaction_layout(
        640, 360, 6, REACTOR, INSET, samples([REACTOR, (520., 180., 80., 100.)]),
    )
    assert 'crop=202:240:' in plan['video_filter']
    assert ':120,scale=1080:1280' in plan['video_filter']


def test_wide_shot_retains_both_guests_and_source_content_is_not_top_cropped():
    wide = reaction_layout.build_reaction_layout(
        640, 360, 6, REACTOR, INSET, samples([REACTOR, (100., 170., 40., 50.), (520., 170., 40., 50.)]),
    )
    assert '[part0]split=2[guest0a][guest0b]' in wide['video_filter']
    assert '[g0a][g0b]vstack=inputs=2[p0]' in wide['video_filter']
    assert '[reactor][base1]vstack=inputs=2[vout]' in wide['video_filter']
    _assert_source_crops_fit(wide['video_filter'], 640, 360)
    frames = samples([REACTOR])
    content = reaction_layout.build_reaction_layout(640, 360, 6, REACTOR, INSET, frames)
    assert content['source_runs'][0][2] == 'content'
    assert '[part0]format=rgba,drawbox=x=480:y=0:w=160:h=120:color=black@0:t=fill:replace=1,scale=' in content['video_filter']


def _assert_source_crops_fit(graph, width, height):
    # crop=W:H:X:Y is in source pixels. The blurred plate's crop=540:640 has
    # no origin because it runs after the downscale.
    crops = [
        tuple(int(v) for v in match)
        for match in re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", graph)
    ]
    assert crops
    for w, h, x, y in crops:
        assert w % 2 == 0 and h % 2 == 0
        assert w >= 2 and h >= 2
        assert 0 <= x and x + w <= width
        assert 0 <= y and y + h <= height


def test_wide_guest_panels_that_exceed_the_frame_fall_back_to_one_crop():
    # 1080x1400 is wider than 9:16, so reaction layout runs, but a 9:8 tile
    # around these faces is wider than the frame. ffmpeg would reject it.
    width, height = 1080, 1400
    inset = (860., 0., 220., 200.)
    reactor = (900., 30., 50., 70.)
    guests = [(80., 400., 180., 230.), (700., 400., 180., 230.)]
    plan = reaction_layout.build_reaction_layout(
        width, height, 6, reactor, inset, samples([reactor, *guests]),
    )
    assert "split=2" not in plan["video_filter"]
    assert "crop=1080:1200:0:200" in plan["video_filter"]
    _assert_source_crops_fit(plan["video_filter"], width, height)


def test_under_inset_closeup_is_shrunk_to_the_frame():
    width, height = 1000, 1600
    inset = (700., 0., 300., 250.)
    reactor = (760., 40., 80., 100.)
    subject = (760., 500., 140., 320.)
    plan = reaction_layout.build_reaction_layout(
        width, height, 6, reactor, inset, samples([reactor, subject]),
    )
    assert "crop=1000:1184:0:250" in plan["video_filter"]
    _assert_source_crops_fit(plan["video_filter"], width, height)


def test_source_guest_positions_are_not_pooled_across_camera_cuts():
    frames = samples([REACTOR, (100., 170., 40., 50.), (520., 170., 40., 50.)])
    plan = reaction_layout.build_reaction_layout(640, 360, 6, REACTOR, INSET, frames, [3.])
    assert len(plan['source_runs']) == 2
    assert plan['source_runs'][0][1] == plan['source_runs'][1][0] == 3.


def test_reaction_precedes_cut_heavy_fallback_without_speaker_labels(tmp_path):
    faces = samples([REACTOR, MAIN])
    with (
        patch.object(reframing, 'ffprobe_duration', return_value=6),
        patch.object(reframing, 'analyze_vertical_clip', return_value=([], [1, 2, 3, 4, 5], faces)),
        patch.object(reaction_layout, 'measure_reaction_inset', return_value=INSET),
    ):
        graph, mode, caption_y = reframing.build_vertical_filter_plan(tmp_path / 'clip.mp4', 640, 360)
    assert 'vstack=inputs=2[vout]' in graph
    assert mode == 'complex' and caption_y == pytest.approx(1 / 3)


def test_relocated_hook_title_clears_the_reactor_panel():
    _, events = build_hook_title_ass('Reaction headline', {}, 1080, 1920, 6, 'Arial', 62, position_y=.94)
    assert '\\an2\\pos(540,1805)' in events[0]


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='ffmpeg not installed')
@pytest.mark.parametrize('source_faces', [
    [MAIN], [(100., 170., 40., 50.), (520., 170., 40., 50.)], [],
])
def test_real_reaction_render_separates_inset_and_source_and_keeps_audio(tmp_path, source_faces):
    source, output = tmp_path / 'reaction.mp4', tmp_path / 'stack.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=blue:size=640x360:rate=10:duration=2,drawbox=x=0:y=0:w=320:h=360:color=green:t=fill,drawbox=x=480:y=0:w=160:h=120:color=red:t=fill',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(source),
    ], check=True, capture_output=True)
    frames = samples([REACTOR, *source_faces], count=6)
    assert reaction_layout.measure_reaction_inset(source, 640, 360, 2, REACTOR) == INSET
    layout = reaction_layout.build_reaction_layout(640, 360, 2, REACTOR, INSET, frames)
    ok, w, h = reframing.render_reframed_clip_ffmpeg(source, output, 'vertical', vertical_plan=(layout['video_filter'], 'complex'))
    assert ok and (w, h) == (1080, 1920)
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(output)]))
    assert {s['codec_type'] for s in probe['streams']} == {'video', 'audio'}
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '0.5', '-i', str(output), '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080, 3)
    assert pixels[300, 540, 0] > 200 and pixels[300, 540, 2] < 40
    if len(source_faces) == 2:
        # Different guest regions must land in different panels. Repeating
        # either crop would fail even if the graph and output size were valid.
        assert pixels[950, 540, 1] > 90 and pixels[950, 540, 2] < 40
        assert pixels[1600, 540, 2] > 200 and pixels[1600, 540, 1] < 40
    elif source_faces:
        assert pixels[1200, 540, 1] > 90 and pixels[1200, 540, 2] < 40
    else:
        assert pixels[1200, 270, 1] > 90 and pixels[1200, 270, 2] < 40
    # No copy of the red inset may survive anywhere in the source panel.
    lower = pixels[640:]
    assert not np.any((lower[:, :, 0] > 180) & (lower[:, :, 2] < 60))
