"""Opening review on real transcript fragments from the 2026-10 clip audit (#284)."""

from src.clip_openings import (
    OPENING_HOOK_PENALTY,
    opening_label_seconds,
    opening_text_issue,
    review_opening,
)
from src.media.timeline import _sentence_start_word_index
from src.services.video_service import VideoService


def _words(rows):
    return [{"text": text, "start": start / 1000, "end": end / 1000} for start, end, text in rows]


# Gordon Ramsay scrambled eggs, clip "Never season your eggs" (00:05-00:43).
EGGS = [
    (4615, 4824, "please."),
    (5001, 5756, "Where's—"),
    (5998, 6416, "Tyler?"),
    (6577, 6770, "No."),
    (7348, 7445, "No?"),
    (14563, 14595, "Ah!"),
    (17215, 17504, "So"),
    (17731, 18021, "never"),
    (18021, 18183, "ever"),
    (18183, 18441, "season"),
    (18441, 18570, "eggs"),
    (18586, 18844, "before"),
    (18844, 18909, "you"),
    (18909, 19006, "cook"),
    (19006, 19135, "them,"),
    (19167, 19506, "okay?"),
]

# Huberman interview, clip opening on "Catharsis." (33:28-34:17).
CATHARSIS = [
    (2006960, 2007361, "advice."),
    (2008532, 2009221, "Catharsis."),
    (2009895, 2009959, "You"),
    (2010071, 2010264, "can"),
    (2010777, 2010985, "try"),
    (2011033, 2011130, "to"),
    (2011338, 2011595, "write"),
    (2011675, 2012028, "down"),
]

# Fireship React in 100 Seconds, clip opening "But the main reason..." (01:09-01:50).
REACT = [
    (67921, 68242, "common"),
    (68242, 68354, "use"),
    (68402, 68771, "cases."),
    (69044, 69204, "But"),
    (69204, 69269, "the"),
    (69269, 69413, "main"),
    (69461, 69750, "reason"),
    (69750, 69782, "you"),
    (69782, 69926, "might"),
    (69926, 70070, "want"),
    (70070, 70135, "to"),
    (70151, 70247, "use"),
    (70247, 70776, "React"),
    (70808, 70872, "is"),
    (70888, 71033, "not"),
    (71033, 71129, "the"),
    (71129, 71418, "library"),
    (71450, 72075, "itself,"),
    (74321, 74593, "React"),
    (74593, 75074, "itself"),
]


def test_lead_in_banter_moves_the_start_to_the_promised_rule():
    words = _words(EGGS)
    review = review_opening(words, 5, 43, 15)

    assert review.issue == "fragment"
    assert review.skipped_text == "Where's— Tyler? No. No? Ah!"
    assert review.start == 17
    assert review.first_word_start == 17.215
    assert not review.penalise
    # The renderer's own snap opens the new label on "So never ever season eggs".
    assert words[_sentence_start_word_index(words, review.start, 3.0)]["text"] == "So"


def test_lone_one_word_answer_is_trimmed():
    words = _words(CATHARSIS)
    review = review_opening(words, 33 * 60 + 28, 34 * 60 + 17, 15)

    assert review.issue == "fragment"
    assert review.skipped_text == "Catharsis."
    assert review.start == 33 * 60 + 29
    assert words[_sentence_start_word_index(words, review.start, 3.0)]["text"] == "You"


def test_long_dangling_conjunction_is_penalised_not_cut():
    review = review_opening(_words(REACT), 69, 110, 15)

    assert review.issue == "dangling"
    assert review.start is None
    assert review.penalise


def test_trim_is_refused_when_the_clip_would_get_too_short():
    review = review_opening(_words(EGGS), 5, 30, 15)

    assert review.start is None
    assert review.penalise


def test_previous_sentence_tail_inside_the_label_second_is_not_a_fragment():
    # A floored label often catches the last word of the previous sentence;
    # the renderer already skips it, so the opening is fine.
    words = _words([
        (586100, 586900, "of"),
        (587100, 587500, "light."),
        (587600, 587800, "I"),
        (587800, 588000, "had"),
        (588000, 588300, "a"),
        (588300, 588900, "discussion."),
    ])
    assert review_opening(words, 587, 626, 15).issue is None


def test_short_hook_sentence_before_a_full_one_is_kept():
    words = _words([
        (1000, 1300, "Here's"),
        (1300, 1600, "why."),
        (1700, 1900, "Eggs"),
        (1900, 2100, "keep"),
        (2100, 2400, "cooking."),
    ])
    assert review_opening(words, 1, 40, 15).issue is None


def test_label_falls_back_to_the_next_second_when_needed():
    words = _words([
        (10000, 10200, "No."),
        (10400, 10600, "So"),
        (10600, 11400, "listen"),
        (11400, 11800, "closely."),
    ])
    # Label 10 snaps onto "No.", label 11 walks back to "So".
    assert opening_label_seconds(words, 1) == 11


def test_text_only_classification():
    assert opening_text_issue("Where's— Tyler? No. No? Ah! So never ever season eggs") == "fragment"
    assert opening_text_issue("But the main reason you might want to use React is the ecosystem.") == "dangling"
    assert opening_text_issue("So never ever season eggs before you cook them, okay?") is None


def _transcript(rows):
    return {"words": [{"text": text, "start": start, "end": end} for start, end, text in rows]}


def test_refine_trims_the_eggs_opening_and_demotes_react():
    eggs = {"start_time": "00:05", "end_time": "00:43", "text": "Where's— Tyler? ...",
            "virality_score": 72, "hook_score": 18, "hook_start_time": "00:08", "hook_end_time": "00:11"}
    segments = VideoService._refine_segment_openings([eggs], _transcript(EGGS), "auto")
    assert segments[0]["start_time"] == "00:17"
    assert segments[0]["text"].startswith("So never ever season eggs")
    assert segments[0]["hook_start_time"] is None
    assert segments[0]["virality_score"] == 72

    react = {"start_time": "01:09", "end_time": "01:50", "text": "But the main reason ...",
             "virality_score": 77, "hook_score": 20}
    intro = {"start_time": "00:00", "end_time": "00:41", "text": "React, a JavaScript library ...",
             "virality_score": 68, "hook_score": 18}
    ranked = VideoService._refine_segment_openings([react, intro], _transcript(REACT), "auto")
    assert [segment["start_time"] for segment in ranked] == ["00:00", "01:09"]
    assert react["hook_score"] == 20 - OPENING_HOOK_PENALTY
    assert react["virality_score"] == 77 - OPENING_HOOK_PENALTY

    # A cached analysis is reviewed once; reusing it must not stack penalties.
    again = VideoService._refine_segment_openings(ranked, _transcript(REACT), "auto")
    assert react["virality_score"] == 77 - OPENING_HOOK_PENALTY
    assert [segment["start_time"] for segment in again] == ["00:00", "01:09"]


def test_refine_without_word_timings_penalises_from_text():
    segment = {"start_time": "01:09", "end_time": "01:50", "virality_score": 77, "hook_score": 20,
               "text": "But the main reason you might want to use React is the ecosystem."}
    VideoService._refine_segment_openings([segment], None, "auto")
    assert segment["virality_score"] == 77 - OPENING_HOOK_PENALTY
    assert segment["start_time"] == "01:09"
