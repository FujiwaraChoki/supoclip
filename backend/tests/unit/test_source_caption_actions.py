"""The render acts on the source's own burned-in text: one caption layer, never two on top of each other."""

import json

import pytest

from src import video_utils
from src.media import source_captions
from src.media.source_captions import SourceCaptions

SPEECH = SourceCaptions(band=(0.74, 0.78), left=0.2, right=0.8, kind="speech", coverage=0.9, match=0.9)
TRANSLATION = SourceCaptions(band=(0.88, 0.93), left=0.2, right=0.8, kind="text", coverage=0.9, match=0.0)


@pytest.fixture
def clip(tmp_path, monkeypatch):
    video = tmp_path / "source-video.mp4"
    video.with_suffix(".transcript_cache.json").write_text(json.dumps({
        "version": 2, "language_code": "en", "utterances": [],
        "words": [{"text": "Hello.", "start": 1000, "end": 1400, "confidence": 1.0}],
    }), encoding="utf-8")
    state = {"found": SPEECH, "masked": [], "probed": 0, "words": None}

    def analyze(_path, words):
        state["words"] = words
        return state["found"]

    def mask(path, captions, intervals):
        state["masked"].append((path, captions, list(intervals)))
        return state.get("mask_ok", True)

    def rendered(*_args):
        state["probed"] += 1
        return state.get("rendered_band")

    monkeypatch.setattr(source_captions, "analyze_source_captions", analyze)
    monkeypatch.setattr(source_captions, "mask_caption_band", mask)
    monkeypatch.setattr(source_captions, "detect_rendered_caption_band", rendered)
    monkeypatch.setattr("src.media.reframing.caption_probe_plan", lambda *_a: ("null", "vf"))
    monkeypatch.setattr(video_utils, "ffprobe_duration", lambda _path: 30.0)
    return video, tmp_path / "cut.mp4", state


def _resolve(clip, fmt, size, plan_report, **kwargs):
    video, cut, _state = clip
    plan = ("crop=608:1080:0:0", "vf") if fmt == "vertical" else None
    return video_utils._resolve_source_captions(video, cut, [(0.0, 30.0)], None, fmt, size, plan_report, plan, **kwargs)


def test_a_short_that_captions_its_own_speech_gets_no_second_layer(clip):
    # 9:16 source, nothing cropped: the source's captions are whole and say the speech.
    assert _resolve(clip, "vertical", (1080, 1920), {"kind": "crop"}) == (None, "suppress")
    assert clip[2]["masked"] == []
    assert clip[2]["words"] and clip[2]["words"][0]["text"] == "Hello."     # read against the clip's own words


def test_captions_the_user_edited_are_never_left_off(clip):
    band, action = _resolve(clip, "vertical", (1080, 1920), {"kind": "crop"}, keep_ours=True)
    assert action == "avoid" and band is not None


def test_subtitles_cut_by_the_crop_are_painted_out_and_ours_keep_their_place(clip):
    assert _resolve(clip, "vertical", (1920, 1080), {"kind": "crop"}) == (None, "mask")
    (path, captions, intervals), = clip[2]["masked"]
    assert path == clip[1] and captions is SPEECH and intervals == [(0.0, 30.0)]


def test_only_the_cropped_shots_are_painted(clip):
    report = {"kind": "compositor", "interview_segments": [
        {"start": 0.0, "end": 20.0, "kind": "single"}, {"start": 20.0, "end": 30.0, "kind": "fit"}]}
    assert _resolve(clip, "vertical", (1920, 1080), report)[1] == "mask"
    assert clip[2]["masked"][0][2] == [(0.0, 20.0)]


def test_when_painting_fails_ours_move_clear_of_the_band(clip):
    clip[2]["mask_ok"] = False
    band, action = _resolve(clip, "vertical", (1920, 1080), {"kind": "crop"})
    assert action == "avoid"
    assert band == source_captions.band_through_full_height_crop(SPEECH.band)


def test_a_translation_shown_whole_stays_and_ours_avoid_it(clip):
    clip[2]["found"] = TRANSLATION
    band, action = _resolve(clip, "original", (1920, 1080), {})
    assert (band, action) == (TRANSLATION.band, "avoid")
    assert clip[2]["masked"] == []


LABEL = SourceCaptions(band=(0.88, 0.93), left=0.2, right=0.8, kind="static", coverage=1.0, match=0.0)


def test_a_games_own_subtitles_are_painted_out_of_the_game_panel(clip):
    # The game panel under the streamer's camera crops the width: the game's subtitle line was sliced at both
    # ends while ours were left off for those lines. It is painted out and every line gets a caption.
    clip[2]["found"] = TRANSLATION
    assert _resolve(clip, "vertical", (1920, 1080), {"kind": "gaming"}) == (None, "mask")
    assert clip[2]["masked"][0][2] == [(0.0, 30.0)]
    assert _resolve(clip, "vertical", (1920, 1080), {"kind": "reaction"}) == (None, "mask")


def test_split_layouts_look_at_the_composed_picture_for_a_label(clip):
    clip[2]["found"] = LABEL
    clip[2]["rendered_band"] = (0.55, 0.6)
    assert _resolve(clip, "vertical", (1920, 1080), {"kind": "gaming"}) == ((0.55, 0.6), "avoid")
    assert clip[2]["probed"] == 1 and clip[2]["masked"] == []
    # Not found in the composed picture of a game layout: no guess is better than a wrong row.
    clip[2]["rendered_band"] = None
    assert _resolve(clip, "vertical", (1920, 1080), {"kind": "gaming"}) == (None, "avoid")


def test_no_source_text_changes_nothing(clip):
    clip[2]["found"] = None
    assert _resolve(clip, "vertical", (1920, 1080), {"kind": "crop"}) == (None, "none")


def test_captions_that_fit_inside_every_crop_are_whole_and_ours_are_left_off(clip):
    # A portrait picture set in a 16:9 frame: the scene plan's crops hold the source's lines.
    clip[2]["found"] = SourceCaptions(band=(0.57, 0.67), left=0.37, right=0.63, kind="speech", coverage=0.94, match=0.94)
    report = {"kind": "crop", "crop_windows": [(0.0, 30.0, 0.342, 0.658)]}
    assert _resolve(clip, "vertical", (1920, 1080), report) == (None, "suppress")
    assert clip[2]["masked"] == []


def test_a_crop_to_one_side_still_paints_the_lines_it_cuts(clip):
    clip[2]["found"] = SourceCaptions(band=(0.80, 0.88), left=0.37, right=0.63, kind="speech", coverage=0.94, match=0.94)
    report = {"kind": "crop", "crop_windows": [(0.0, 10.0, 0.342, 0.658), (10.0, 30.0, 0.05, 0.366)]}
    assert _resolve(clip, "vertical", (1920, 1080), report) == (None, "mask")
    (_path, _captions, intervals), = clip[2]["masked"]
    assert intervals == [(0.0, 30.0)]              # ours are drawn for every line, so every shot is painted


def test_whole_captions_that_come_and_go_are_painted_not_doubled(clip):
    # A creator's captions on a few phrases, inside the crop: too few to carry the clip, so ours are drawn,
    # and theirs must go or the phrase is on screen twice.
    clip[2]["found"] = SourceCaptions(band=(0.94, 0.98), left=0.40, right=0.60, kind="speech", coverage=0.19, match=0.67)
    report = {"kind": "crop", "crop_windows": [(0.0, 30.0, 0.342, 0.658)]}
    assert _resolve(clip, "vertical", (1920, 1080), report) == (None, "mask")
