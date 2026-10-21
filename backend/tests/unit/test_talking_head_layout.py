from pathlib import Path
from unittest.mock import patch

from src.media.captions import build_hook_title_ass
from src.media.reframing import build_vertical_filter_plan
from src.media.talking_head_layout import (
    caption_y_below_subject,
    choose_talking_head_layout,
)


def _frames(boxes, count=12):
    return [(index / 3.0, [tuple(box) for box in boxes]) for index in range(count)]


def test_low_webcam_face_is_staged_above_the_lower_half():
    # Face centre sits at 63% of a 1080p frame: the full-height 9:16 crop
    # would leave it there and scale the slice about 1.8x.
    layout = choose_talking_head_layout(
        1920, 1080, _frames([(800, 560, 280, 240)]), scene_cuts=[]
    )

    assert layout is not None
    assert layout["kind"] == "stage"
    assert layout["upscale"] <= 1.35
    assert 0.32 <= layout["face_y"] <= 0.46
    assert layout["caption_y"] > layout["video_bottom"] - 0.02
    assert "gblur" in layout["video_filter"]
    assert "[vout]" in layout["video_filter"]


def test_side_by_side_webcams_use_a_stacked_layout():
    layout = choose_talking_head_layout(
        1920,
        1080,
        _frames([(160, 480, 220, 200), (1100, 500, 220, 200)]),
        scene_cuts=[],
    )

    assert layout is not None
    assert layout["kind"] == "stack"
    assert layout["upscale"] <= 1.25
    assert layout["face_y"] < 0.40
    assert 0.48 < layout["caption_y"] < 0.62
    assert "overlay=0:168" in layout["video_filter"]
    assert "overlay=0:1120" in layout["video_filter"]


def test_gallery_is_shown_in_full_instead_of_zooming_one_tile():
    faces = [(40, 40, 140, 120), (700, 40, 140, 120), (40, 500, 140, 120), (700, 500, 140, 120)]
    layout = choose_talking_head_layout(1920, 1080, _frames(faces), scene_cuts=[])

    assert layout is not None
    assert layout["kind"] == "fit"
    assert layout["upscale"] < 1
    assert "force_original_aspect_ratio=decrease" in layout["video_filter"]


def test_high_closeup_keeps_the_tracked_crop():
    assert (
        choose_talking_head_layout(
            1920, 1080, _frames([(700, 80, 400, 360)]), scene_cuts=[]
        )
        is None
    )


def test_podcast_two_shot_with_high_faces_is_not_stacked():
    assert (
        choose_talking_head_layout(
            1920,
            1080,
            _frames([(200, 60, 300, 280), (1100, 70, 300, 280)]),
            scene_cuts=[],
        )
        is None
    )


def test_captions_move_only_when_they_would_cover_the_face():
    assert caption_y_below_subject(0.45) == 0.80
    lowered = caption_y_below_subject(0.74)
    assert lowered > 0.80
    assert lowered <= 0.84


def test_vertical_plan_stacks_a_call_before_speaker_closeups(tmp_path):
    samples = _frames([(160, 480, 220, 200), (1100, 500, 220, 200)])
    track = [(t, 270.0, 0.03) for t, _ in samples]
    with (
        patch("src.media.reframing.ffprobe_duration", return_value=8.0),
        patch(
            "src.media.reframing.analyze_vertical_clip",
            return_value=(track, [], samples),
        ),
    ):
        video_filter, mode, caption_y = build_vertical_filter_plan(
            tmp_path / "clip.mp4",
            1920,
            1080,
            [{"speaker": "A", "start": 0, "end": 4}, {"speaker": "B", "start": 4, "end": 8}],
            [{"text": "hello", "start": 0.2, "end": 0.6, "speaker": "A"}],
        )

    assert mode == "complex"
    assert "overlay=0:1120" in video_filter
    assert 0.48 < caption_y < 0.62


def test_hook_title_is_larger_than_the_captions():
    style, events = build_hook_title_ass(
        "Somebody has to be boss of the algorithm",
        {"font_color": "#FFFFFF", "stroke_color": "#000000", "stroke_width": 3, "shadow": True},
        1080,
        1920,
        30.0,
        "Arial",
        62,
    )

    size = int(style.split(",")[2])
    assert size >= 78
    assert size > 62
    assert events and "SOMEBODY" not in events[0]
    assert "boss" in events[0]


def test_long_hook_title_shrinks_below_the_preferred_size():
    word = "Donaudampfschifffahrtsgesellschaft"
    style, events = build_hook_title_ass(
        word,
        {"font_color": "#FFFFFF", "stroke_color": "#000000", "stroke_width": 3, "shadow": True},
        1080,
        1920,
        30.0,
        "Arial",
        62,
    )

    size = int(style.split(",")[2])
    usable = 1080 - 2 * max(48, int(1080 * 0.07))
    assert size < 64
    assert size == max(1, int(usable / (len(word) * 0.52)))
    assert word in events[0]
