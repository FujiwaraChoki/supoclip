"""edit_fx.mapping: S ↔ E for cues, beats and words (studio segments)."""

import random

import pytest
from pydantic import TypeAdapter

from src.edit_fx.contracts import Beat, FxCue
from src.edit_fx.mapping import inverse_mapped_cues, mapped_beats, mapped_cues, mapped_words_e
from src.editor_document import EditDocument, continuous_segments, mapped_broll, mapped_words

CUE = TypeAdapter(FxCue)
FOOTAGE = {
    "id": 42, "width": 1080, "height": 1920, "duration": 10,
    "fileUrl": "https://videos.pexels.com/video-files/42/42-hd_1080_1920_25fps.mp4",
    "pageUrl": "https://www.pexels.com/video/42/", "thumbnail": "https://images.pexels.com/videos/42/pictures/preview-0.jpg",
    "credit": "Some Filmmaker",
}


def span(cue_id, start, end, kind="flash"):
    return CUE.validate_python({"kind": kind, "id": cue_id, "start": start, "end": end})


def sfx(cue_id, at):
    return CUE.validate_python({"kind": "sfx", "id": cue_id, "start": at, "end": at, "sound": "pop"})


def doc(segments, broll=()):
    return EditDocument.model_validate({
        "segments": [{"id": f"s{i}", "start": a, "end": b} for i, (a, b) in enumerate(segments)],
        "broll": [{"id": f"b{i}", "start": a, "end": b, "offset": 0, "footage": FOOTAGE}
                  for i, (a, b) in enumerate(broll)],
    })


def segs(document):
    return [(s.id, s.start, s.end) for s in continuous_segments(document.segments)]


def as_pieces(cues):
    return sorted((round(c.start, 9), round(c.end - c.start, 9)) for c in cues)


def broll_pieces(document):
    return sorted((round(p["start"], 9), round(p["duration"], 9)) for p in mapped_broll(document))


# --- mirrors mapped_broll -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("segments,clip,expected_starts", [
    (((0, 2), (3, 6)), (1, 4), [1, 2]),               # test_broll_follows_cuts_and_keeps_its_footage_position
    (((3, 6), (0, 3), (0, 3)), (1, 2), [4, 7]),       # test_broll_follows_reordered_and_duplicated_segments
    (((0, 2), (4, 6)), (2.5, 3.5), []),               # test_cut_out_broll_disappears
])
def test_span_cues_follow_segments_like_broll(segments, clip, expected_starts):
    document = doc(segments, broll=[clip])
    cues = mapped_cues(segs(document), [span("c", *clip)])
    assert [c.start for c in cues] == expected_starts
    assert as_pieces(cues) == broll_pieces(document)


def test_span_cues_match_broll_on_random_documents():
    rng = random.Random(7)
    for _ in range(200):
        bounds = sorted(rng.sample(range(0, 40), 6))
        pieces = [(bounds[i] / 2, bounds[i + 1] / 2) for i in range(0, 6, 2)]
        rng.shuffle(pieces)
        if rng.random() < 0.3:
            pieces.append(pieces[0])                      # a repeat
        start = rng.randint(0, 30) / 2
        clip = (start, start + rng.choice([1.5, 2.0, 3.5, 6.0]))
        document = doc(pieces, broll=[clip])
        assert as_pieces(mapped_cues(segs(document), [span("c", *clip)])) == broll_pieces(document)


# --- ids, instants, ordering --------------------------------------------------------------------------------------

def test_generation_identity():
    cues = [span("b0.flash", 1.0, 1.2), sfx("b0.sfx", 1.0), span("b1.accent", 0.5, 3.0)]
    out = mapped_cues([("clip", 0.0, 6.0)], cues)
    assert [(c.id, c.start, c.end) for c in out] == [("b1.accent", 0.5, 3.0), ("b0.flash", 1.0, 1.2),
                                                     ("b0.sfx", 1.0, 1.0)]
    assert out[1] == cues[0]


def test_repeats_get_at_suffixes():
    out = mapped_cues([("s0", 3, 6), ("s1", 0, 3), ("s2", 0, 3)], [span("x", 1, 2), sfx("p", 1)])
    assert [(c.id, c.start) for c in out] == [("p", 4.0), ("x", 4.0), ("p@1", 7.0), ("x@1", 7.0)]


def test_long_ids_are_truncated_to_fit():
    out = mapped_cues([("a", 0, 2), ("b", 0, 2)], [span("x" * 32, 0.5, 0.7)])
    assert [c.id for c in out] == ["x" * 32, "x" * 30 + "@1"]


def test_instant_lead_and_segment_end_rules():
    segments = [("s0", 0.0, 2.0), ("s1", 4.0, 6.0)]
    assert [c.start for c in mapped_cues(segments, [sfx("lead", 3.97)])] == [2.0]       # clamped to s1's start
    assert mapped_cues(segments, [sfx("early", 3.9)]) == []
    end = mapped_cues(segments, [sfx("end", 2.0)])
    assert [(c.id, c.start) for c in end] == [("end", 2.0)]                            # belongs to s0 (AR-m4)
    contiguous = mapped_cues([("s0", 0.0, 2.0), ("s1", 2.0, 4.0)], [sfx("j", 2.0)])
    assert [(c.id, c.start) for c in contiguous] == [("j", 2.0)]                       # the next segment owns it
    last = mapped_cues([("s0", 1.0, 3.0)], [sfx("t", 3.0)])
    assert [c.start for c in last] == [2.0]


def test_instant_near_a_small_gap_is_emitted_once():
    # A pause cut of 30 ms: the lead of s1 reaches back into s0, but an instant s0 plays (or ends) must not be
    # emitted again by s1, or the bus would double the SFX and the warp would hold twice.
    segments = [("s0", 0.0, 5.0), ("s1", 5.03, 10.0)]
    cues = [sfx("end", 5.0), sfx("inside", 4.98), CUE.validate_python(
        {"kind": "freeze", "id": "f", "start": 4.99, "end": 4.99, "hold": 0.5})]
    mapped = mapped_cues(segments, cues)
    assert sorted((c.id, round(c.start, 6)) for c in mapped) == [("end", 5.0), ("f", 4.99), ("inside", 4.98)]
    # Inside the removed gap itself the lead still rescues it, once.
    assert [(c.id, c.start) for c in mapped_cues(segments, [sfx("gap", 5.01)])] == [("gap", 5.0)]


def test_min_piece():
    segments = [("s0", 0.0, 2.0), ("s1", 4.0, 6.0)]
    assert as_pieces(mapped_cues(segments, [span("c", 1.98, 4.5)])) == [(2.0, 0.5)]
    assert as_pieces(mapped_cues(segments, [span("c", 1.9, 4.2)], min_piece=0.25)) == []


def test_mapped_beats_keep_source_times():
    beat = Beat(id="b0", kind="emphasis", start=1.0, end=4.0, src_start=101.0, src_end=104.0, words=("big",))
    out = mapped_beats([("s0", 0, 2), ("s1", 3, 6)], [beat])
    assert [(b.id, b.start, b.end, b.src_start) for b in out] == [("b0", 1.0, 2.0, 101.0), ("b0@1", 2.0, 3.0, 101.0)]


# --- words ------------------------------------------------------------------------------------------------------

def test_mapped_words_e_matches_editor_mapped_words():
    words = [{"id": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.4, "text": f"word{i}"} for i in range(12)]
    document = EditDocument.model_validate({
        "segments": [{"id": "s0", "start": 0, "end": 2}, {"id": "s1", "start": 3.2, "end": 6}], "words": words,
    })
    editor = [(w["id"], round(w["start"], 9), round(w["end"], 9)) for w in mapped_words(document)]
    ours = mapped_words_e(segs(document), words)
    assert [(w["id"], round(w["start"], 9), round(w["end"], 9)) for w in ours] == editor
    assert {w["run"] for w in ours} == {"s0", "s1"}


def test_mapped_words_e_repeats_and_blanks():
    words = [{"id": "w0", "start": 0.1, "end": 0.5, "text": "hey", "src_start": 10.1},
             {"id": "w1", "start": 0.6, "end": 0.9, "text": "  "}]
    out = mapped_words_e([("a", 0, 1), ("b", 0, 1)], words)
    assert [(w["id"], w["start"], w["run"], w["src_start"]) for w in out] == [("w0", 0.1, "a", 10.1),
                                                                               ("w0@1", 1.1, "b", 10.1)]


# --- inverse ------------------------------------------------------------------------------------------------------

def test_inverse_recovers_cues_inside_segments():
    segments = [("s0", 4.0, 7.0), ("s1", 0.0, 3.0), ("s2", 9.0, 10.0)]
    cues = [span("a", 4.5, 6.0), sfx("b", 1.0), span("c", 9.1, 9.9), sfx("e", 0.0), sfx("f", 10.0)]
    back = inverse_mapped_cues(segments, mapped_cues(segments, cues))
    by_id = {c.id: c for c in back}
    for cue in cues:
        assert cue.id in by_id
        assert by_id[cue.id].start == pytest.approx(cue.start) and by_id[cue.id].end == pytest.approx(cue.end)


def test_inverse_puts_a_junction_instant_in_the_incoming_segment():
    # E 3.0 is both the end of s0 and the start of s1. Resolver output there (a transition, the sfx on the first
    # word after a cut) belongs to the incoming segment, so the inverse picks s1. Forward mapping gives E 3.0 again.
    segments = [("s0", 4.0, 7.0), ("s1", 0.0, 3.0)]
    back = inverse_mapped_cues(segments, [sfx("t", 3.0)])
    assert [c.start for c in back] == [0.0]
    assert [c.start for c in mapped_cues(segments, back)] == [3.0]


def test_inverse_round_trip_property():
    rng = random.Random(11)
    for _ in range(100):
        bounds = sorted(rng.sample(range(0, 60), 6))
        segments = [(f"s{i}", bounds[2 * i] / 2, bounds[2 * i + 1] / 2) for i in range(3)]
        rng.shuffle(segments)
        cues = []
        for k, (_, a, b) in enumerate(segments):
            start = rng.uniform(a, b - 0.2)
            cues.append(span(f"c{k}", start, min(b, start + rng.uniform(0.1, 2))))
            cues.append(sfx(f"p{k}", rng.uniform(a, b - 0.01)))
        back = {c.id: c for c in inverse_mapped_cues(segments, mapped_cues(segments, cues))}
        for cue in cues:
            assert back[cue.id].start == pytest.approx(cue.start, abs=1e-9)
            assert back[cue.id].end == pytest.approx(cue.end, abs=1e-9)


def test_inverse_splits_spans_at_junctions():
    segments = [("s0", 0.0, 2.0), ("s1", 5.0, 8.0)]
    back = inverse_mapped_cues(segments, [span("z", 1.0, 3.5)])
    assert [(c.id, c.start, c.end) for c in back] == [("z", 1.0, 2.0), ("z~1", 5.0, 6.5)]


def test_inverse_emits_repeated_segments_once():
    segments = [("s0", 0.0, 3.0), ("s1", 0.0, 3.0)]
    back = inverse_mapped_cues(segments, [span("a", 1.0, 2.0), span("b", 4.0, 5.0), sfx("p", 4.0), sfx("q", 0.5)])
    assert [(c.id, c.start, c.end) for c in back] == [("q", 0.5, 0.5), ("a", 1.0, 2.0)]


def test_inverse_end_of_timeline_and_empty():
    assert [c.start for c in inverse_mapped_cues([("s0", 2.0, 4.0)], [sfx("t", 2.0)])] == [4.0]
    assert inverse_mapped_cues([], [sfx("t", 1.0)]) == []
    assert inverse_mapped_cues([("s0", 0, 1)], [sfx("t", 5.0)]) == []
