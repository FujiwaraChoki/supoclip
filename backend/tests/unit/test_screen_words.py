"""Captions leave out what the picture already says, and sounds that are not speech."""

from pathlib import Path

import pytest

from src.media import screen_words as sw


def _words(text, start=0.0, step=0.4, speaker=None, confidence=0.95):
    return [
        {"text": word, "start": start + i * step, "end": start + i * step + 0.3, "confidence": confidence,
         "speaker": speaker}
        for i, word in enumerate(text.split())
    ]


def _screen(monkeypatch, lines):
    """``lines``: (from, to, text) shown on screen between those seconds."""
    looks = []

    def read(_path, seconds):
        looks.append(seconds)
        return [token for a, b, text in lines if a <= seconds <= b for token in sw._tokens(text)]

    monkeypatch.setattr(sw, "read_screen", read)
    return looks


def test_dialogue_the_game_subtitles_gets_no_captions(monkeypatch):
    spoken = ("This fellow operates a growing house and always pays Brian with product. "
              "He left it inside the trunk.")
    words = _words(spoken)
    # The OCR reads the first line and misses the second: the voice is the game's, so all of it goes.
    _screen(monkeypatch, [(0.0, 5.0, "This fellow operates a growing house and always pays Brian with product.")])
    assert sw.spoken_caption_words(Path("clip.mp4"), words) == []


def test_text_all_over_the_screen_does_not_silence_a_streamer(monkeypatch):
    # A quest log full of ordinary words under somebody who never reads it: short words prove nothing.
    words = _words("okay so over there you can see the boat and I think that is where we need to go for the next one")
    _screen(monkeypatch, [(0.0, 30.0, "Deliver the package over there before the timer ends and you can collect "
                                      "the reward for the next one, then return to where we started")])
    assert sw.spoken_caption_words(Path("clip.mp4"), words) == words


def test_without_speaker_labels_a_long_stretch_off_screen_keeps_its_captions(monkeypatch):
    game = _words("Listen carefully because nobody leaves until every single dollar returns safely tonight")
    own = _words("honestly that whole cutscene looked incredible compared against anything rockstar released "
                 "before, absolutely unbelievable lighting everywhere tonight", start=8.0)
    _screen(monkeypatch, [(0.0, 6.0, "Listen carefully because nobody leaves until every single dollar returns safely tonight")])
    kept = sw.spoken_caption_words(Path("clip.mp4"), game + own)
    assert kept and kept[0]["text"] == "honestly" and all(word in own for word in kept)


def test_a_streamer_talking_over_the_game_keeps_every_caption(monkeypatch):
    words = _words("I am in the food court and this looks like a legit mall okay let us order some food")
    _screen(monkeypatch, [(0.0, 20.0, "Turn left 270 ft Drive to Hamlet with Cal")])
    assert sw.spoken_caption_words(Path("clip.mp4"), words) == words


def test_with_speaker_labels_only_the_subtitled_voice_goes(monkeypatch):
    game = _words("You are carrying plenty of money, deposit everything inside the nearest machine quickly", speaker="B")
    streamer = _words("oh my god chat did you see that that was insane", start=8.0, speaker="A")
    _screen(monkeypatch, [(0.0, 6.0, "You are carrying plenty of money, deposit everything inside the nearest machine quickly.")])
    assert sw.spoken_caption_words(Path("clip.mp4"), game + streamer) == streamer


def test_one_line_read_from_the_screen_goes_and_the_rest_stays(monkeypatch):
    own = _words("okay so what does it say here let me read this one for you all right")
    read_out = _words("mission passed respect plus", start=10.0)
    after = _words("that is hilarious honestly i did not expect that from this game at all", start=14.0)
    _screen(monkeypatch, [(9.5, 12.5, "MISSION PASSED! RESPECT PLUS")])
    assert sw.spoken_caption_words(Path("clip.mp4"), own + read_out + after) == own + after


def test_a_long_word_misread_by_one_letter_still_counts(monkeypatch):
    words = _words("Defend the bachelor party from the shark before it reaches them")
    _screen(monkeypatch, [(0.0, 9.0, "Defend the bachelar party from the shork before it reoches them")])
    assert sw.spoken_caption_words(Path("clip.mp4"), words) == []


def test_a_lone_uncertain_word_in_silence_is_a_sound(monkeypatch):
    stray = _words("Vacation!", start=1.0, confidence=0.2)
    speech = _words("right so today we are going to compare both games", start=12.0)
    _screen(monkeypatch, [])
    assert sw.spoken_caption_words(Path("clip.mp4"), stray + speech) == speech
    # The same word said with confidence is kept.
    sure = _words("Vacation!", start=1.0, confidence=0.9)
    assert sw.spoken_caption_words(Path("clip.mp4"), sure + speech) == sure + speech


def test_a_picture_that_cannot_be_read_keeps_the_captions(monkeypatch):
    words = _words("This guy runs a grow house and pays Brian in weed")

    def broken(_path, _seconds):
        raise RuntimeError("tesseract is not installed")

    monkeypatch.setattr(sw, "read_screen", broken)
    assert sw.spoken_caption_words(Path("clip.mp4"), words) == words


def test_the_picture_is_read_a_bounded_number_of_times(monkeypatch):
    words = _words(" ".join(f"word{i}" for i in range(400)), step=0.5)
    looks = _screen(monkeypatch, [])
    sw.spoken_caption_words(Path("clip.mp4"), words)
    assert 1 <= len(looks) <= sw.MAX_SCREEN_READS


def test_the_transcript_is_filtered_for_captions_only(monkeypatch):
    data = {"words": [{"text": w["text"], "start": int(w["start"] * 1000), "end": int(w["end"] * 1000)}
                      for w in _words("This fellow operates a growing house and always pays Brian with product")]}
    _screen(monkeypatch, [(0.0, 9.0, "This fellow operates a growing house and always pays Brian with product.")])
    filtered = sw.without_screen_words(Path("clip.mp4"), data, [(0.0, 10.0)])
    assert filtered["words"] == [] and len(data["words"]) == 12
    _screen(monkeypatch, [])
    assert sw.without_screen_words(Path("clip.mp4"), data, [(0.0, 10.0)]) is data
