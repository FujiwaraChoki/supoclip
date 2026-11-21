"""Caption lines cut from the transcript follow speech: they end at a pause and at the end of a sentence.

Lines used to be four words by count. Where a long pause fell inside one (a laugh, a silent stretch of
gameplay), the line stayed on screen for the whole pause, with the words after it readable long before
they were spoken, and most lines joined the end of one sentence to the start of the next. The word
timings below are made up to that shape (ms).
"""

import re

from src.media import captions
from src.media.captions import build_assemblyai_ass_subtitles, ends_sentence


def _words(*items):
    return [{"text": text, "start": start, "end": end, "confidence": 0.99} for text, start, end in items]


# The last line before a pause of almost 19 seconds, then the next speaker.
LONG_PAUSE = _words(
    ("That", 7280, 7520), ("was", 7520, 7680), ("the", 7680, 7880), ("plan.", 7880, 8240),
    ("Now", 26890, 27050), ("tell", 27050, 27210), ("me", 27210, 27330), ("what", 27330, 27450),
    ("went", 27450, 27530), ("wrong", 27530, 27650), ("today", 27650, 27810),
)
# Two speakers, three sentences inside what used to be two four-word lines.
TWO_SPEAKERS = _words(
    ("Did", 9440, 9720), ("you", 9720, 9880), ("see", 9880, 10000), ("where", 10000, 10120),
    ("it", 10120, 10400), ("went?", 10400, 10640), ("Out", 11360, 11680), ("back,", 11680, 11960),
    ("I", 11960, 12120), ("think.", 12120, 12480), ("No,", 12960, 13360),
)

_EVENT = re.compile(r"^Dialogue: \d+,(\d+):(\d+):(\d+\.\d+),(\d+):(\d+):(\d+\.\d+),Default,[^,]*,\d+,\d+,\d+,[^,]*,(.*)$")


def _lines(tmp_path, monkeypatch, words, start_ms, end_ms, **kwargs):
    """[(shown from, shown until, text)] of each caption line, in clip seconds."""
    monkeypatch.setattr(captions, "load_cached_transcript_data", lambda _path: {"words": words})
    out = tmp_path / "captions.ass"
    assert build_assemblyai_ass_subtitles(
        tmp_path / "clip.mp4", start_ms / 1000, end_ms / 1000, 1080, 1920, out, **kwargs)
    lines = {}
    for raw in out.read_text(encoding="utf-8").splitlines():
        match = _EVENT.match(raw)
        if not match:
            continue
        h1, m1, s1, h2, m2, s2, body = match.groups()
        start = int(h1) * 3600 + int(m1) * 60 + float(s1)
        end = int(h2) * 3600 + int(m2) * 60 + float(s2)
        text = re.sub(r"\{[^}]*\}", "", body).strip().upper()
        first, last, _ = lines.get(text, (start, end, text))
        lines[text] = (min(first, start), max(last, end), text)
    return sorted(lines.values())


def test_a_line_does_not_wait_out_a_long_pause(tmp_path, monkeypatch):
    lines = _lines(tmp_path, monkeypatch, LONG_PAUSE, 7000, 28000)
    texts = [text for _, _, text in lines]
    assert texts == ["THAT WAS THE PLAN.", "NOW TELL ME WHAT", "WENT WRONG TODAY"]  # 7 words: 4 + 3
    first_start, first_end, _ = lines[0]
    # "plan." ends 1.24 s into the clip; the line is gone well before the next speaker 19.9 s in.
    assert first_end <= 1.24 + captions.CAPTION_LINE_MIN_HOLD_SECONDS
    assert lines[1][0] >= 19.8
    # No line is on screen for more than its own speech plus the short hold.
    for start, end, text in lines:
        assert end - start < 2.0, text


def test_a_line_never_joins_two_sentences_or_speakers(tmp_path, monkeypatch):
    lines = _lines(tmp_path, monkeypatch, TWO_SPEAKERS, 9340, 13500)
    texts = [text for _, _, text in lines]
    assert texts == ["DID YOU SEE", "WHERE IT WENT?", "OUT BACK, I THINK.", "NO,"]  # 6 words: 3 + 3
    for text in texts:
        inner = text.split()[:-1]
        assert not any(ends_sentence(word) for word in inner), text


def test_short_lines_stay_readable_and_bridge_small_gaps(tmp_path, monkeypatch):
    lines = _lines(tmp_path, monkeypatch, TWO_SPEAKERS, 9340, 13500)
    by_text = {text: (start, end) for start, end, text in lines}
    # "went?" ends at 1.30 s and "Out" starts at 2.02 s: the gap is longer than the bridge, so the line
    # leaves after its last word instead of sitting there with a stale highlight.
    start, end = by_text["WHERE IT WENT?"]
    assert 1.29 <= end <= 1.31
    # The first line runs straight into the second: no blink between them.
    assert by_text["DID YOU SEE"][1] == by_text["WHERE IT WENT?"][0]
    # Lines never overlap.
    ordered = sorted(by_text.values())
    assert all(a[1] <= b[0] + 1e-6 for a, b in zip(ordered, ordered[1:]))


def test_editor_words_keep_their_fixed_groups(tmp_path):
    """The studio keys per-line placements by fixed groups of ``wordsPerLine``: its lines are not regrouped."""
    words = [{"text": t, "start": s, "end": e} for t, s, e in (
        ("was", 0.0, 0.3), ("the", 0.3, 0.5), ("plan.", 0.5, 0.9), ("Now", 5.0, 5.2), ("tell", 5.2, 5.4))]
    out = tmp_path / "editor.ass"
    assert build_assemblyai_ass_subtitles(tmp_path / "clip.mp4", 0, 6, 1080, 1920, out, caption_words=words)
    text = out.read_text(encoding="utf-8")
    bodies = {re.sub(r"\{[^}]*\}", "", m.group(7)).strip().upper() for m in map(_EVENT.match, text.splitlines()) if m}
    assert bodies == {"WAS THE PLAN. NOW", "TELL"}


def test_a_line_too_brief_to_read_joins_a_neighbour(tmp_path, monkeypatch):
    """Fast speech: "Yeah." lasts a fifth of a second before the next sentence starts."""
    words = _words(
        ("Yeah.", 1000, 1150), ("Let's", 1200, 1400), ("go", 1400, 1600), ("now.", 1600, 1900),
        ("We", 2000, 2150), ("have", 2150, 2300), ("to", 2300, 2400), ("leave.", 2400, 2750), ("Okay.", 2800, 2950),
        ("Fine.", 5000, 5150), ("Move", 5200, 5400), ("it", 5400, 5500), ("over", 5500, 5700), ("there.", 5700, 6000),
    )
    lines = _lines(tmp_path, monkeypatch, words, 900, 6100)
    texts = [text for _, _, text in lines]
    # "Yeah." joins the sentence after it; "Okay." is the last line before a pause and keeps its hold;
    # "Fine." cannot join a four-word sentence, so it stays on its own.
    assert texts == ["YEAH. LET'S GO NOW.", "WE HAVE TO LEAVE.", "OKAY.", "FINE.", "MOVE IT OVER THERE."]
    by_text = {text: end - start for start, end, text in lines}
    assert by_text["OKAY."] >= captions.CAPTION_LINE_MIN_HOLD_SECONDS - 0.011
    # A pause is never bridged: nothing joins "Okay." (before a 2 s pause) to "Fine." after it.
    assert all("OKAY. FINE" not in text for text in texts)


def test_the_last_line_of_a_passage_ends_with_its_last_word():
    """A planned clip jumps to its next moment right after a passage: no hold or bridge across that cut."""
    lines = [(0.0, 0.2), (0.25, 0.5), (0.6, 0.9)]
    assert captions._line_display_ends(lines) == [0.25, 0.6, 1.0]
    assert captions._line_display_ends(lines, before_cut=[1]) == [0.25, 0.5, 1.0]


def test_words_between_two_breaks_are_shared_evenly():
    bounds = lambda count, breaks=(): list(captions._chunk_bounds(count, 4, list(breaks), True))
    assert bounds(5) == [(0, 3), (3, 5)]
    assert bounds(8) == [(0, 4), (4, 8)]
    assert bounds(9) == [(0, 3), (3, 6), (6, 9)]
    assert bounds(6, [2]) == [(0, 2), (2, 6)]
    # Fixed groups (the studio, FX caption edits) are unchanged.
    assert list(captions._chunk_bounds(5, 4, [])) == [(0, 4), (4, 5)]


def test_ends_sentence():
    assert all(ends_sentence(word) for word in ("here?", "guess.", "go!", 'done."', "好。", "wait…"))
    assert not any(ends_sentence(word) for word in ("luck,", "Mr.", "Dr.", "J.", "vs.", "why", "5.2", ""))
