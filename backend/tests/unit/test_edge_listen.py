"""A second opinion on the words around a clip's cuts: what is corrected, and what the audio must show first.

Synthetic words and levels only. Each case is a way a clip used to end on a stray word, lose the start of
its first one, or stop in the middle of a sentence because the transcript was a little wrong at the cut.
"""

import json

import pytest

from src.media import edge_listen, sentence_bounds, transcription
from src.media.edge_listen import Heard, align, corrections, loud_share, run_on_boundary, sound_edge
from src.media.sentence_bounds import unit_index_for


def _words(*items):
    return [{"text": text, "start": start, "end": end} for text, start, end in items]


def _heard(*items):
    return [Heard(text, start, end) for text, start, end in items]


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    transcription._WORD_FIXES.clear()
    transcription._WORD_FIX_ORIGINS.clear()
    transcription._WORD_FIX_GENERATION.clear()
    edge_listen._SPEECH_LIMITS.clear()
    edge_listen._HEARD.clear()
    sentence_bounds._INDEX_CACHE.clear()


def test_words_are_lined_up_through_spelling_differences_and_missing_words():
    pairs = align(["Plan", "B", "is", "ready.", "Go", "ahead."], ["Plan", "B", "is", "ready,", "go", "ahead", "now."])
    assert pairs == {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5}
    # A word heard as something else is not matched; its neighbours are.
    pairs = align(["out", "of", "here,", "buddy.", "Stop"], ["out", "of", "here,", "pal.", "Stop"])
    assert pairs == {0: 0, 1: 1, 2: 2, 4: 4}
    # A different spelling of the same word is.
    assert align(["going", "to", "leave"], ["goin", "to", "leave"]) == {0: 0, 1: 1, 2: 2}


def test_a_word_timed_late_next_to_the_cut_is_proposed_at_its_heard_time():
    # "...is ready. Go ahead." The transcript times "ready." to 9.38 and "Go" from 9.38; they are heard
    # ending at 9.12 and starting at 9.26. The clip ended on "Go".
    words = _words(("Plan", 8.26, 8.74), ("B", 8.74, 8.94), ("is", 8.94, 9.10), ("ready.", 9.10, 9.38),
                   ("Go", 9.38, 9.54), ("ahead.", 9.54, 9.82), ("Okay,", 12.0, 12.3))
    heard = _heard(("Just", 7.5, 7.9), ("Plan", 8.18, 8.44), ("B", 8.44, 8.76), ("is", 8.76, 8.90),
                   ("ready,", 8.90, 9.12), ("go", 9.26, 9.34), ("ahead", 9.34, 9.52), ("now.", 9.52, 9.72))
    fixes, before, after = corrections(words, heard, edge=3, closing=True)
    assert fixes[3]["end"] == pytest.approx(9.12) and fixes[4]["start"] == pytest.approx(9.26)
    assert before is None and after is None            # both neighbours are the transcript's own words


def test_a_heard_time_is_not_trusted_where_the_transcripts_differ_or_the_word_is_stretched():
    # The recogniser skipped "I have to." and stamped "Before" over it: its times drift onto the wrong word.
    words = _words(("done.", 5.63, 5.95), ("I", 6.11, 6.35), ("have", 6.35, 6.47), ("to.", 6.47, 6.67),
                   ("Before", 6.67, 6.87), ("you", 6.87, 7.03), ("go,", 7.03, 7.27))
    heard = _heard(("nearly", 5.17, 5.39), ("done.", 5.39, 5.67), ("Before", 6.05, 6.35), ("you", 6.35, 6.97),
                   ("go", 6.97, 7.17), ("though,", 7.17, 7.31))
    fixes, _before, _after = corrections(words, heard, edge=4)
    assert "start" not in fixes.get(4, {})
    # A word stretched back over the pause before it is no measurement either.
    words = _words(("folks.", 7.17, 7.73), ("This", 8.37, 8.65), ("is", 8.65, 8.81), ("it,", 8.81, 9.05), ("folks.", 9.05, 9.33))
    heard = _heard(("Ooh,", 6.0, 6.3), ("folks", 6.33, 7.41), ("this", 7.41, 8.59), ("is", 8.59, 8.77), ("it", 8.77, 8.91),
                   ("folks", 8.91, 9.15), ("I", 9.15, 9.35))
    fixes, _before, _after = corrections(words, heard, edge=1)
    assert "start" not in fixes.get(1, {})


def test_an_edge_word_heard_as_something_else_is_placed_where_its_stand_in_was_heard():
    # "...out of here, buddy. Stop waiting": the last word is heard as "pal." half a second before the
    # transcript's time for it, and "Stop" where the transcript has the last word. The clip ended on "Stop".
    words = _words(("out", 6.45, 6.64), ("of", 6.64, 6.92), ("here,", 6.95, 7.18), ("buddy.", 7.44, 7.60),
                   ("Stop", 7.86, 8.07), ("waiting", 8.17, 8.46))
    heard = _heard(("way", 5.83, 6.31), ("out", 6.31, 6.50), ("of", 6.50, 6.59), ("here,", 6.59, 6.93),
                   ("pal.", 6.95, 7.07), ("Stop", 7.33, 7.63), ("waiting", 7.63, 8.31), ("for", 8.31, 8.51))
    fixes, _before, _after = corrections(words, heard, edge=3, closing=True)
    assert fixes[3]["start"] == pytest.approx(6.95) and fixes[3]["end"] == pytest.approx(7.07)
    assert fixes[4]["start"] == pytest.approx(7.33)


def test_a_full_stop_written_a_few_words_late_is_moved_to_where_the_sentence_is_heard_to_end():
    # "...which does matter if you're reading this aloud. There is so much more..." is heard as
    # "...which does matter. If you're reading this aloud, there is so much more...".
    words = _words(("sounds", 2.9, 3.16), ("right,", 3.16, 3.42), ("which", 3.56, 3.72), ("does", 3.72, 3.94),
                   ("matter", 3.94, 4.28), ("if", 4.54, 4.66), ("you're", 4.66, 4.82), ("reading", 4.82, 5.04),
                   ("this", 5.04, 5.32), ("aloud.", 5.32, 5.62), ("There", 5.76, 5.88), ("is", 5.88, 6.08), ("so", 6.08, 6.4))
    heard = _heard(("voice", 2.6, 2.9), ("sounds", 2.9, 3.16), ("right,", 3.16, 3.42), ("which", 3.56, 3.72),
                   ("does", 3.72, 3.94), ("matter.", 3.94, 4.28), ("If", 4.54, 4.66), ("you're", 4.66, 4.82),
                   ("reading", 4.82, 5.04), ("this", 5.04, 5.32), ("aloud,", 5.32, 5.62), ("there", 5.76, 5.88),
                   ("is", 5.88, 6.08), ("so", 6.08, 6.4), ("much", 6.4, 6.8))
    fixes, _before, _after = corrections(words, heard, edge=9, closing=True)
    assert fixes[4]["text"] == "matter." and fixes[5]["text"] == "If"
    assert fixes[9]["text"] == "aloud," and fixes[10]["text"] == "there"
    # One stray word, after a word the two transcripts spell differently: "...nice to see LAN part. Two."
    # is heard as "...a quarter C LAM. Part two. 45 seconds".
    stray = _words(("still", 4.15, 4.31), ("nice", 4.31, 4.55), ("to", 4.55, 4.63), ("see", 4.63, 4.75), ("LAN", 4.75, 5.35),
                   ("part.", 6.31, 6.75), ("Two.", 6.75, 7.51), ("45", 8.23, 8.83), ("seconds", 8.83, 9.27))
    said = _heard(("was", 3.93, 4.05), ("still", 4.05, 4.23), ("a", 4.23, 4.33), ("quarter", 4.33, 4.43), ("C", 4.43, 4.67),
                  ("LAM.", 4.67, 5.03), ("Part", 6.27, 6.73), ("two.", 6.73, 7.19), ("45", 8.23, 8.69), ("seconds", 8.69, 9.15))
    texts = {position: change["text"] for position, change in corrections(stray, said, edge=5, closing=True)[0].items() if "text" in change}
    assert texts == {4: "LAN.", 5: "Part", 6: "two."}
    # A recogniser that merely runs the clip's last sentence into the next is no evidence: nothing changes.
    plain = _heard(*[(item.text.replace("matter.", "matter").replace("If", "if"), item.start, item.end) for item in heard])
    assert corrections(words, plain, edge=9, closing=True)[0] == {}
    # Nor is a sentence end heard without a pause (a shouted repetition): "...los, lauf gleich los. Du".
    shout = _words(("gleich", 7.22, 7.5), ("los,", 7.5, 7.78), ("lauf", 7.94, 8.46), ("gleich", 8.46, 8.74),
                   ("los.", 8.74, 8.9), ("Du", 8.9, 9.1), ("Timo.", 9.1, 9.78))
    heard = _heard(("Lauf", 6.8, 7.06), ("gleich", 7.06, 7.34), ("los.", 7.34, 7.64), ("Lauf", 7.7, 8.38),
                   ("gleich", 8.38, 8.62), ("los,", 8.62, 8.92), ("du", 9.0, 9.06), ("Timmo!", 9.06, 9.42), ("Ha", 9.6, 9.8))
    assert not any("text" in change for change in corrections(shout, heard, edge=4, closing=True)[0].values())
    # And it is only looked for at a clip's end.
    assert not any("text" in change for change in corrections(words, heard[:0] or _heard(), edge=9)[0].values())


def test_speech_the_transcript_has_no_words_for_next_to_the_edge_word_sets_a_limit():
    # Another voice says "No, not yet." right up to the clip's first word; the transcript does not have it.
    words = _words(("we", 8.5, 8.86), ("are.", 8.86, 9.14), ("So", 9.94, 10.34), ("this", 10.42, 10.7), ("whole", 10.7, 10.94))
    heard = _heard(("where", 7.9, 8.2), ("we", 8.5, 8.78), ("are.", 8.78, 8.9), ("No,", 8.96, 9.08), ("not", 9.44, 9.7),
                   ("yet.", 9.7, 9.94), ("So", 10.0, 10.1), ("this", 10.1, 10.62), ("whole", 10.62, 10.82), ("plan", 10.82, 10.96))
    _fixes, before, after = corrections(words, heard, edge=2)
    assert before == pytest.approx(9.94) and after is None


def _levels(*runs):
    return [level for seconds, level in runs for _ in range(int(round(seconds / 0.005)))]


def test_the_audio_decides_where_a_word_starts_and_ends():
    # Speech, a real gap, speech.
    levels = _levels((0.5, -10.0), (0.10, -45.0), (0.5, -10.0))
    assert sound_edge(levels, 0.005, near=0.48, onset=False) == pytest.approx(0.50, abs=0.01)
    assert sound_edge(levels, 0.005, near=0.55, onset=True) == pytest.approx(0.60, abs=0.01)
    # The 40 ms gaps inside a word ("G-T-A", a stop consonant) do not end it.
    spelled = _levels((0.3, -12.0), (0.04, -40.0), (0.2, -12.0), (0.04, -40.0), (0.3, -12.0))
    assert sound_edge(spelled, 0.005, near=0.32, onset=False) is None
    # Nothing starts or stops near the heard time: no measurement.
    assert sound_edge(_levels((1.2, -10.0)), 0.005, near=0.6, onset=True) is None
    assert loud_share(levels, 0.005, 0.52, 0.58) == 0.0 and loud_share(levels, 0.005, 0.1, 0.4) == 1.0


def test_a_move_must_be_supported_by_what_it_adds_or_removes(monkeypatch):
    # A soft first syllable: quiet until 6.70, then "Pho-" rising, loud from 6.95. The recogniser puts the
    # word at 7.13 and a dip inside it reads as a start at 7.20. Starting there would cut the word in half.
    def levels(_video, start, end):
        import numpy as np
        times = np.arange(start, end, 0.005)
        return np.array([-48.0 if t < 6.70 else (-26.0 if t < 6.95 else (-40.0 if 7.16 <= t < 7.20 else -12.0)) for t in times])

    monkeypatch.setattr(sentence_bounds, "_audio_levels", levels)
    words = _words(("it.", 5.69, 5.97), ("Photosynthesis", 6.61, 7.57), ("is", 7.57, 7.77))
    assert edge_listen._measured("video.mp4", "start", 7.13) == pytest.approx(7.21, abs=0.02)
    assert edge_listen.measure_fixes("video.mp4", words, {1: {"start": 7.13}}) == {}
    # An earlier start is taken only when there is sound in what it adds.
    assert not edge_listen._supported("video.mp4", "start", 6.61, 6.2)
    assert edge_listen._supported("video.mp4", "start", 7.3, 6.96)


def _sidecar(tmp_path, words):
    video = tmp_path / "video.mp4"
    payload = {"version": 2, "language_code": "en", "utterances": [], "words": [
        {"text": text, "start": int(round(start * 1000)), "end": int(round(end * 1000)), "confidence": 0.99, "speaker": "A"}
        for text, start, end in words
    ]}
    video.with_suffix(".transcript_cache.json").write_text(json.dumps(payload), encoding="utf-8")
    return video


def test_corrections_are_kept_for_the_latest_videos_only_and_caches_never_match_again(tmp_path, monkeypatch):
    monkeypatch.setattr(transcription, "WORD_FIX_VIDEOS", 2)
    videos = []
    for name in ("first", "second", "third"):
        folder = tmp_path / name
        folder.mkdir()
        videos.append(_sidecar(folder, [("ready.", 9.10, 9.38), ("Go", 9.38, 9.54)]))
        assert transcription.register_word_fixes(videos[-1], {(9100, 9380, "ready."): {"end": 9.14}})
    # The oldest video's corrections are gone: its words read as the file has them again.
    assert transcription.load_cached_transcript_data(videos[0])["words"][0]["end"] == 9380
    assert transcription.load_cached_transcript_data(videos[2])["words"][0]["end"] == 9140
    # Its generation is not: an index built on the corrected words must not pass for the current one.
    assert transcription.word_fix_generation(videos[0]) == 1
    assert transcription.register_word_fixes(videos[0], {(9100, 9380, "ready."): {"end": 9.14}})
    assert transcription.word_fix_generation(videos[0]) == 2


def test_recorded_corrections_change_every_later_read_of_the_transcript_and_never_the_file(tmp_path):
    video = _sidecar(tmp_path, [("is", 8.94, 9.10), ("ready.", 9.10, 9.38), ("Go", 9.38, 9.54), ("ahead.", 9.54, 9.82)])
    before = video.with_suffix(".transcript_cache.json").read_text()
    assert transcription.register_word_fixes(video, {(9100, 9380, "ready."): {"end": 9.14}, (9380, 9540, "Go"): {"start": 9.26}})
    words = transcription.load_cached_transcript_data(video)["words"]
    assert (words[1]["start"], words[1]["end"]) == (9100, 9140) and words[2]["start"] == 9260
    assert video.with_suffix(".transcript_cache.json").read_text() == before
    assert transcription.word_fix_generation(video) == 1
    assert transcription.corrected_word_time(video, 9.38, "end") == pytest.approx(9.14)
    assert transcription.corrected_word_time(video, 9.10, "start") == pytest.approx(9.10)
    # A correction that would overlap a neighbour gives way to it, and is reported as it was applied.
    assert transcription.register_word_fixes(video, {(9100, 9140, "ready."): {"start": 9.00}})
    assert transcription.load_cached_transcript_data(video)["words"][1]["start"] == 9100      # "is" ends at 9.10
    assert transcription.corrected_word_time(video, 9.10, "start") == pytest.approx(9.10)
    # The same correction again changes nothing; a correction of the corrected word is the same word.
    assert not transcription.register_word_fixes(video, {(9100, 9380, "ready."): {"end": 9.14}})
    assert transcription.register_word_fixes(video, {(9100, 9140, "ready."): {"text": "ready,"}})
    assert transcription.load_cached_transcript_data(video)["words"][1]["text"] == "ready,"


def _clip_video(tmp_path):
    return _sidecar(tmp_path, [
        ("We", 1.0, 1.2), ("tried", 1.2, 1.5), ("the", 1.5, 1.6), ("new", 1.6, 1.8), ("recipe.", 1.8, 2.3),
        ("It", 3.0, 3.2), ("is", 3.2, 3.4), ("ready.", 3.4, 3.98), ("Go", 3.98, 4.14), ("taste", 4.14, 4.3), ("it.", 4.3, 4.6),
        ("Next", 6.0, 6.3), ("up", 6.3, 6.5), ("is", 6.5, 6.6), ("the", 6.6, 6.7), ("soup.", 6.7, 7.1),
    ])


def test_a_clip_no_longer_ends_on_the_first_word_of_the_next_sentence(tmp_path, monkeypatch):
    video = _clip_video(tmp_path)
    monkeypatch.setattr(edge_listen, "available", lambda: True)
    monkeypatch.setattr(edge_listen, "hear", lambda *_a: _heard(
        ("the", 1.5, 1.6), ("new", 1.6, 1.8), ("recipe.", 1.8, 2.3), ("It", 3.0, 3.2), ("is", 3.2, 3.4),
        ("ready,", 3.4, 3.72), ("go", 3.84, 3.94), ("taste", 3.94, 4.2), ("it.", 4.2, 4.5), ("Next", 6.0, 6.3)))
    monkeypatch.setattr(edge_listen, "_measured", lambda _video, _kind, heard_time: heard_time)
    monkeypatch.setattr(edge_listen, "_supported", lambda *_a, **_k: True)
    index = unit_index_for(video)
    edges = (index.unit(1).start, index.unit(2).end)
    assert index.unit(2).text == "It is ready."
    cut_in, cut_out = sentence_bounds.sentence_bounds(video, 1.0, 4.0, 60.0, edges=edges)
    assert cut_out <= 3.84                       # "ready." is heard to end at 3.72 and "go" to start at 3.84
    words = transcription.load_cached_transcript_data(video)["words"]
    assert words[7]["end"] == words[8]["start"] == 3780       # the two run together: one boundary, between them
    # Asked again, with the transcript already corrected, the answer is the same.
    assert sentence_bounds.sentence_bounds(video, 1.0, 4.0, 60.0, edges=edges) == (cut_in, cut_out)


def test_without_a_recogniser_nothing_changes(tmp_path, monkeypatch):
    video = _clip_video(tmp_path)
    monkeypatch.setattr(edge_listen, "available", lambda: False)
    index = unit_index_for(video)
    assert not edge_listen.retime_around(video, index, 1, 2)
    assert transcription.word_fix_generation(video) == 0


def test_listening_is_off_on_and_automatic_only_with_the_model_on_disk(tmp_path, monkeypatch):
    from src.media import common

    monkeypatch.setattr(common, "_WHISPER_AVAILABLE", True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(edge_listen, "_model_name", lambda: "small")
    for setting, expected in (("off", False), ("on", True), ("auto", False)):
        monkeypatch.setattr(edge_listen, "_setting", lambda value=setting: value)
        assert edge_listen.available() is expected
    (tmp_path / "whisper").mkdir()
    (tmp_path / "whisper" / "small.pt").write_bytes(b"x")
    monkeypatch.setattr(edge_listen, "_setting", lambda: "auto")
    assert edge_listen.available() is True
    monkeypatch.setattr(common, "_WHISPER_AVAILABLE", False)
    assert edge_listen.available() is False


def test_between_words_that_run_together_the_heard_boundary_is_the_guide():
    # "...dejo entrar. Pero si no entra": no breath between "entrar." and a softly spoken "Pero". The transcript
    # puts the boundary at 2.95; it is heard at 2.74-2.82. The quietest instant near the transcript's
    # boundary was inside "Pero", and the clip opened on "ro".
    words = _words(("Yo", 2.11, 2.39), ("dejo", 2.39, 2.67), ("entrar.", 2.67, 2.95), ("Pero", 2.95, 3.07),
                   ("si", 3.07, 3.23), ("no", 3.23, 3.35), ("entra,", 3.35, 3.91))
    heard = _heard(("carta", 1.5, 1.9), ("yo", 2.04, 2.24), ("dejo", 2.24, 2.48), ("entrar,", 2.48, 2.74),
                   ("pero", 2.82, 2.94), ("si", 2.94, 3.04), ("no", 3.04, 3.14), ("entra", 3.14, 3.4), ("tambien", 3.5, 3.9))
    assert run_on_boundary(words, heard, edge=3, closing=False) == pytest.approx((2.74, 2.82))
    # At a clip's end it is the boundary to the word after the last one.
    assert run_on_boundary(words, heard, edge=2, closing=True) == pytest.approx((2.74, 2.82))
    # Words with a pause between them have silence to cut in: nothing to do here.
    paused = _words(("entrar.", 2.4, 2.6), ("Pero", 2.95, 3.07), ("si", 3.07, 3.23))
    assert run_on_boundary(paused, heard, edge=1, closing=False) is None
    # Nor when the neighbour was not heard next to the edge word.
    other = _heard(("dejo", 2.24, 2.48), ("entrar,", 2.48, 2.74), ("eh", 2.76, 2.8), ("pero", 2.82, 2.94), ("si", 2.94, 3.04))
    assert run_on_boundary(words, other, edge=3, closing=False) is None


def test_a_run_on_opening_is_cut_at_the_heard_boundary_and_the_word_outside_is_only_moved_towards_the_clip(tmp_path, monkeypatch):
    video = _sidecar(tmp_path, [
        ("We", 0.5, 0.7), ("left", 0.7, 0.9), ("early.", 0.9, 1.3),
        ("Yo", 2.11, 2.39), ("dejo", 2.39, 2.67), ("entrar.", 2.67, 2.95), ("Pero", 2.95, 3.07), ("si", 3.07, 3.23),
        ("no", 3.23, 3.35), ("entra.", 3.35, 3.91), ("Bien", 4.2, 4.45), ("hecho.", 4.45, 4.8),
        ("Next", 6.0, 6.3), ("up.", 6.3, 6.6),
    ])
    monkeypatch.setattr(edge_listen, "available", lambda: True)
    # The word after the clip's last one is heard a little later than written: that must not move the cut.
    monkeypatch.setattr(edge_listen, "hear", lambda *_a: _heard(
        ("early", 0.9, 1.3), ("yo", 2.04, 2.24), ("dejo", 2.24, 2.48), ("entrar,", 2.48, 2.74), ("pero", 2.82, 2.94),
        ("si", 2.94, 3.04), ("no", 3.04, 3.14), ("entra.", 3.14, 3.9), ("Bien", 4.32, 4.5), ("hecho.", 4.5, 4.8), ("Next", 6.0, 6.3)))
    monkeypatch.setattr(edge_listen, "_measured", lambda _video, _kind, heard_time: heard_time)
    monkeypatch.setattr(edge_listen, "_supported", lambda *_a, **_k: True)
    monkeypatch.setattr(sentence_bounds, "quietest_instant", lambda _video, start, end: (start + end) / 2)
    index = unit_index_for(video)
    opening = next(unit.id for unit in index.units if unit.text.startswith("Pero"))
    assert edge_listen.retime_around(video, index, opening, opening)
    words = transcription.load_cached_transcript_data(video)["words"]
    assert words[5]["end"] == words[6]["start"] == 2780            # "entrar." | "Pero", inside the heard boundary
    assert words[10]["start"] == 4200                                # "Bien" stays: later is away from the clip
