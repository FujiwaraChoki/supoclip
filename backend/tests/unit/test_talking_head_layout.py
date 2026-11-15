import re
from pathlib import Path
from unittest.mock import patch

from src.media.captions import build_hook_title_ass
from src.media.reframing import build_vertical_filter_plan
from src.media.scene_context import SceneEvidence, context_scene_cuts
from src.media.talking_head_layout import (
    caption_y_below_subject,
    choose_talking_head_layout,
)


def _frames(boxes, count=12):
    return [(index / 3.0, [tuple(box) for box in boxes]) for index in range(count)]


def _crop_safe_evidence_with_one_context_cut(samples):
    """Confident close-up evidence whose only scene cut comes from context_scene_cuts."""
    cut_time = samples[len(samples) // 2][0]
    evidence = [
        SceneEvidence(
            t,
            face_area=0.05,
            face_span=0.2,
            face_count=1,
            cut=t == cut_time,
            difference=50 if t == cut_time else 8,
        )
        for t, _ in samples
    ]
    cuts = context_scene_cuts(evidence)
    assert cuts == [cut_time]
    return evidence, cuts


def _plan_with_context(samples, evidence, cuts, track_x=960.0):
    track = [(t, track_x, 0.05) for t, _ in samples]
    return patch("src.media.reframing.ffprobe_duration", return_value=8.0), patch(
        "src.media.reframing.analyze_vertical_clip",
        return_value=(track, cuts, samples, evidence),
    )


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


def test_one_context_cut_still_stages_a_low_webcam(tmp_path):
    samples = _frames([(800, 560, 280, 240)])
    evidence, cuts = _crop_safe_evidence_with_one_context_cut(samples)
    expected = choose_talking_head_layout(1920, 1080, samples, cuts)
    assert expected is not None and expected["kind"] == "stage"
    duration, analyzed = _plan_with_context(samples, evidence, cuts)
    with duration, analyzed:
        video_filter, mode, caption_y = build_vertical_filter_plan(tmp_path / "clip.mp4", 1920, 1080)
    assert (video_filter, mode, caption_y) == (expected["video_filter"], "complex", expected["caption_y"])


def test_one_context_cut_still_stacks_a_call(tmp_path):
    samples = _frames([(160, 480, 220, 200), (1100, 500, 220, 200)])
    evidence, cuts = _crop_safe_evidence_with_one_context_cut(samples)
    expected = choose_talking_head_layout(1920, 1080, samples, cuts)
    assert expected is not None and expected["kind"] == "stack"
    duration, analyzed = _plan_with_context(samples, evidence, cuts, track_x=270.0)
    with duration, analyzed:
        video_filter, mode, caption_y = build_vertical_filter_plan(tmp_path / "clip.mp4", 1920, 1080)
    assert (video_filter, mode, caption_y) == (expected["video_filter"], "complex", expected["caption_y"])
    assert "overlay=0:1120" in video_filter


def test_one_context_cut_on_a_static_closeup_still_uses_ken_burns(tmp_path):
    samples = _frames([(700, 80, 400, 360)])
    evidence, cuts = _crop_safe_evidence_with_one_context_cut(samples)
    assert choose_talking_head_layout(1920, 1080, samples, cuts) is None
    duration, analyzed = _plan_with_context(samples, evidence, cuts, track_x=900.0)
    with duration, analyzed:
        video_filter, mode, _caption_y = build_vertical_filter_plan(tmp_path / "clip.mp4", 1920, 1080)
    assert mode == "vf"
    assert "zoompan" in video_filter


def test_more_than_four_cuts_skip_stage_and_stack():
    low = _frames([(800, 560, 280, 240)])
    call = _frames([(160, 480, 220, 200), (1100, 500, 220, 200)])
    assert choose_talking_head_layout(1920, 1080, low, scene_cuts=[1, 2, 3, 4])["kind"] == "stage"
    assert choose_talking_head_layout(1920, 1080, call, scene_cuts=[1, 2, 3, 4])["kind"] == "stack"
    assert choose_talking_head_layout(1920, 1080, low, scene_cuts=[1, 2, 3, 4, 5]) is None
    assert choose_talking_head_layout(1920, 1080, call, scene_cuts=[1, 2, 3, 4, 5]) is None


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


def test_hook_title_can_be_centred_on_a_stacked_seam():
    template = {"font_color": "#FFFFFF", "stroke_color": "#000000", "stroke_width": 3}
    _, events = build_hook_title_ass("Boss of the algorithm", template, 1080, 1920, 30.0, "Arial", 62, center_y=0.5)
    assert "\\an5\\pos(540,960)" in events[0]
    _, default = build_hook_title_ass("Boss of the algorithm", template, 1080, 1920, 30.0, "Arial", 62)
    assert "\\pos(" not in default[0]


# A host at a desk, talking to camera (1280x720): the face box is a third of
# the frame tall, with a band of room above the head (top at 22.5%).
HOST_720 = (546, 162, 182, 247)


def _moving(boxes_at, count=36):
    return [(index / 3.0, [tuple(boxes_at(index))]) for index in range(count)]


def test_lone_presenter_with_headroom_gets_a_tight_solo_crop_not_a_stage():
    layout = choose_talking_head_layout(1280, 720, _frames([HOST_720], count=36), scene_cuts=[])

    assert layout is not None
    assert layout["kind"] == "solo"
    graph = layout["video_filter"]
    # A crop scaled to fill the frame: no blurred fill, no letterbox band.
    assert "gblur" not in graph and "overlay" not in graph
    assert graph.endswith("[vout]")
    match = re.search(r"crop=(\d+):(\d+):x=(\d+):y=(\d+)", graph)
    assert match, graph
    crop_w, crop_h, crop_x, _ = map(int, match.groups())
    assert crop_h == 720 and abs(crop_w / crop_h - 9 / 16) < 0.01
    # The face stays whole inside the crop, centred.
    assert crop_x <= HOST_720[0] and crop_x + crop_w >= HOST_720[0] + HOST_720[2]
    # Eyes near the upper third: the face-box centre sits around 0.36-0.42.
    assert 0.3 <= layout["face_y"] <= 0.45
    assert layout["upscale"] <= 2.7
    assert layout["video_bottom"] == 1.0
    assert 0.78 <= layout["caption_y"] <= 0.84


def test_solo_crop_on_a_1080p_source_stays_within_the_sharp_upscale():
    # A medium shot: face a sixth of the frame tall, top at 30%.
    layout = choose_talking_head_layout(
        1920, 1080, _frames([(860, 330, 150, 180)], count=36), scene_cuts=[]
    )

    assert layout is not None and layout["kind"] == "solo"
    # 2.25x, give or take rounding the crop to even pixels.
    assert layout["upscale"] <= 2.26
    assert layout["face_y"] <= 0.45
    crop_h = int(re.search(r"crop=\d+:(\d+):", layout["video_filter"]).group(1))
    # Tighter than the full height: head and shoulders, not the whole desk.
    assert crop_h < 1080


def test_small_face_with_headroom_is_still_staged():
    # Under SINGLE_MIN_FACE_H_FRAC (11% of the frame): a crop would invent a
    # close-up from a few pixels, so the wider stage stays.
    layout = choose_talking_head_layout(
        1920, 1080, _frames([(900, 300, 90, 100)], count=36), scene_cuts=[]
    )
    assert layout is not None and layout["kind"] == "stage"


def test_face_too_low_to_lift_within_the_upscale_cap_is_still_staged():
    # The webcam face of test_low_webcam_face_is_staged_above_the_lower_half:
    # the tightest allowed crop would leave its centre below mid-frame.
    layout = choose_talking_head_layout(
        1920, 1080, _frames([(800, 560, 280, 240)], count=36), scene_cuts=[]
    )
    assert layout is not None and layout["kind"] == "stage"


def test_low_resolution_source_is_staged_instead_of_cropped_to_mush():
    # 640x360: any 9:16 crop is a 5x enlargement.
    layout = choose_talking_head_layout(
        640, 360, _frames([(273, 81, 91, 124)], count=36), scene_cuts=[]
    )
    assert layout is not None and layout["kind"] == "stage"


def test_a_second_person_for_part_of_the_clip_keeps_the_stage():
    # A guest sits in for four seconds (under the 40% that marks a two-shot
    # clip): the solo crop would cut them off, so the clip is staged as before.
    samples = [
        (i / 3.0, [HOST_720] + ([(120, 170, 170, 230)] if 12 <= i < 24 else []))
        for i in range(36)
    ]
    layout = choose_talking_head_layout(1280, 720, samples, scene_cuts=[])
    assert layout is not None and layout["kind"] == "stage"


def test_solo_crop_follows_a_presenter_who_moves():
    # The presenter drifts across the frame: the crop pans (an expression in
    # time), never jumping from sample to sample.
    layout = choose_talking_head_layout(
        1280, 720, _moving(lambda i: (420 + i * 7, 170, 180, 245)), scene_cuts=[]
    )
    assert layout is not None and layout["kind"] == "solo"
    assert re.search(r"crop=\d+:\d+:x='[^']*t[^']*'", layout["video_filter"])


def test_solo_crop_reframes_at_a_jump_cut_and_reuses_a_setup():
    # Wide-ish then closer framing after a jump cut at 4 s, then back to the
    # first framing at 8 s: each shot is composed on its own, and both
    # returns to the first setup share one zoom.
    first, second = HOST_720, (520, 150, 230, 300)
    samples = [
        (i / 3.0, [first if i < 12 or i >= 24 else second]) for i in range(36)
    ]
    layout = choose_talking_head_layout(1280, 720, samples, scene_cuts=[4.0, 8.0], duration=12.0)
    assert layout is not None and layout["kind"] == "solo"
    graph = layout["video_filter"]
    assert "concat=n=3" in graph
    crops = re.findall(r"crop=(\d+:\d+):x=(\d+)", graph)
    assert len(crops) == 3 and crops[0] == crops[2]


def test_high_closeup_still_keeps_the_tracked_crop_without_a_solo_plan():
    # Unchanged: a face already high in the frame is left to the tracked crop.
    assert choose_talking_head_layout(1280, 720, _frames([(520, 60, 240, 300)], count=36)) is None


def test_vertical_plan_uses_the_solo_crop_for_a_lone_presenter(tmp_path):
    samples = _frames([HOST_720], count=36)
    track = [(t, 637.0, 0.05) for t, _ in samples]
    report = {}
    with (
        patch("src.media.reframing.ffprobe_duration", return_value=12.0),
        patch("src.media.reframing.analyze_vertical_clip", return_value=(track, [], samples)),
    ):
        video_filter, mode, caption_y = build_vertical_filter_plan(
            tmp_path / "clip.mp4", 1280, 720, report=report
        )
    assert report["kind"] == "solo"
    assert mode == "complex" and video_filter.endswith("[vout]")
    assert "gblur" not in video_filter
    assert caption_y is not None and caption_y >= 0.78


def test_solo_layout_is_a_content_kind_the_editor_bakes_beside_the_source():
    from src.services.editor_service import CONTENT_LAYOUT_KINDS, PRESERVED_LAYOUT_KINDS

    assert "solo" in CONTENT_LAYOUT_KINDS
    assert "solo" not in PRESERVED_LAYOUT_KINDS
