"""Classic captions in a stacked two-shot sit on the seam between the halves, not on the lower person's face."""

import re

from src import video_utils
from src.media import captions
from src.media.captions import build_assemblyai_ass_subtitles


def _words(*items):
    return [{"text": text, "start": start, "end": end, "confidence": 0.99} for text, start, end in items]


# Six sentences of four words (ms), so lines by count and lines by sentence are the same lines. The two-shot
# runs from 3 s to 7.5 s of the clip.
def _sentence(name, start, length=1000):
    step = length // 4
    return [(name, start, start + step), ("goes", start + step, start + 2 * step),
            ("like", start + 2 * step, start + 3 * step), ("this.", start + 3 * step, start + length)]


WORDS = _words(*_sentence("First", 200), *_sentence("Second", 1700), *_sentence("Third", 3200),
               *_sentence("Fourth", 4700), *_sentence("Fifth", 6600, 1400), *_sentence("Sixth", 8600))
PAIR = (3.0, 7.5, 960)
_EVENT = re.compile(r"^Dialogue: \d+,[^,]*,[^,]*,Default,[^,]*,\d+,\d+,\d+,[^,]*,(.*)$")


def _rows(tmp_path, monkeypatch, **kwargs):
    """{line text: the y of its \\pos}."""
    monkeypatch.setattr(captions, "load_cached_transcript_data", lambda _path: {"words": WORDS})
    out = tmp_path / "captions.ass"
    assert build_assemblyai_ass_subtitles(tmp_path / "clip.mp4", 0.0, 10.0, 1080, 1920, out, **kwargs)
    rows = {}
    for raw in out.read_text(encoding="utf-8").splitlines():
        match = _EVENT.match(raw)
        if not match:
            continue
        body = match.group(1)
        text = re.sub(r"\{[^}]*\}", "", body).strip().upper()
        rows.setdefault(text, set()).add(int(re.search(r"\\pos\(\d+,(\d+)\)", body).group(1)))
    return rows


def test_lines_spoken_in_a_two_shot_sit_on_the_seam(tmp_path, monkeypatch):
    plain = _rows(tmp_path, monkeypatch)
    (clip_row,) = {y for ys in plain.values() for y in ys}
    assert clip_row > 1400                                           # the clip's row: under the lowest face
    rows = _rows(tmp_path, monkeypatch, caption_rows=[PAIR])
    assert rows["THIRD GOES LIKE THIS."] == {960} and rows["FOURTH GOES LIKE THIS."] == {960}
    # Before and after the two-shot the clip's row is kept.
    assert rows["FIRST GOES LIKE THIS."] == {clip_row} and rows["SECOND GOES LIKE THIS."] == {clip_row} and rows["SIXTH GOES LIKE THIS."] == {clip_row}
    # A line that runs past the end of the two-shot would end up on the next shot's face: it keeps the clip's row.
    assert rows["FIFTH GOES LIKE THIS."] == {clip_row}


def test_the_seam_belongs_to_the_hook_title_while_it_shows(tmp_path, monkeypatch):
    whole = (0.0, 10.0, 960)
    rows = _rows(tmp_path, monkeypatch, caption_rows=[whole], caption_rows_after_hook=True, hook_title="A title on the seam")
    (clip_row,) = rows["FIRST GOES LIKE THIS."]
    assert clip_row != 960 and rows["SECOND GOES LIKE THIS."] == {clip_row} and rows["THIRD GOES LIKE THIS."] == {clip_row}
    assert rows["FOURTH GOES LIKE THIS."] == {960} and rows["SIXTH GOES LIKE THIS."] == {960}   # the title is gone after 4 s
    # Without a title on the seam every line may use it.
    assert _rows(tmp_path, monkeypatch, caption_rows=[whole], hook_title="A title at the top")["FIRST GOES LIKE THIS."] == {960}


def test_only_interview_two_shots_of_a_second_or_more_get_a_row():
    report = {"kind": "interview", "interview_segments": [
        {"start": 0.0, "end": 4.0, "kind": "single"}, {"start": 4.0, "end": 9.0, "kind": "pair"},
        {"start": 9.0, "end": 9.6, "kind": "pair"}, {"start": 9.6, "end": 12.0, "kind": "fit"},
    ]}
    assert video_utils._pair_caption_rows_kwargs(report, 1920) == {
        "caption_rows": [(4.0, 9.0, 960)], "caption_rows_after_hook": False}
    assert video_utils._pair_caption_rows_kwargs({**report, "hook_center_y": 0.5}, 1920)["caption_rows_after_hook"]
    assert video_utils._pair_caption_rows_kwargs({**report, "kind": "gaming"}, 1920) == {}
    assert video_utils._pair_caption_rows_kwargs({"kind": "interview", "interview_segments": report["interview_segments"][:1]}, 1920) == {}
