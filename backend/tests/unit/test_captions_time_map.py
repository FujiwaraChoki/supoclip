"""ASS ``time_map`` / ``censor_spans`` kwargs and the shared ``hook_title_window`` (FINAL §6.1 item 6; WP12c).

Legacy output without the new kwargs is pinned byte-for-byte by ``test_dynamic_edits_legacy_pins.py``; these tests
check that the kwargs do exactly what they say and nothing when absent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.caption_templates import CAPTION_TEMPLATES
from src.edit_fx.contracts import FreezeCue, SpeedCue
from src.edit_fx.warp import build_warp
from src.media import captions
from src.media.common import HOOK_TITLE_MIN_SECONDS, HOOK_TITLE_SECONDS, hook_title_window

LEGACY_TEMPLATES = tuple(CAPTION_TEMPLATES)
DIALOGUE = re.compile(r"^Dialogue: (\d+),(\d+):(\d\d):(\d\d\.\d\d),(\d+):(\d\d):(\d\d\.\d\d),(.*)$")
WORDS = [
    {"text": text, "start": round(0.4 + 0.35 * i, 3), "end": round(0.4 + 0.35 * i + 0.3, 3)}
    for i, text in enumerate(
        "so I told him this is absolutely fucking insane and he just walked out of the room".split()
    )
]
LABELS = [{"text": "First point", "start": 4.5}, {"text": "Second point", "start": 6.0}]


@pytest.fixture(autouse=True)
def no_colour_emoji(monkeypatch):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)


def build(tmp_path: Path, template: str = "default", duration: float = 8.0, **kwargs) -> str:
    out = tmp_path / f"{template}-{len(list(tmp_path.iterdir()))}.ass"
    ok = captions.build_assemblyai_ass_subtitles(
        tmp_path / "missing-source.mp4", 0.0, duration, 1080, 1920, out,
        caption_template=template, caption_words=[dict(word) for word in WORDS],
        hook_title="He walked out of the room", point_labels=LABELS, **kwargs,
    )
    assert ok
    return out.read_text(encoding="utf-8")


def seconds(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


def events(text: str) -> list[tuple[str, float, float, str]]:
    rows = []
    for line in text.splitlines():
        match = DIALOGUE.match(line)
        if match:
            layer, h0, m0, s0, h1, m1, s1, rest = match.groups()
            rows.append((layer, seconds(h0, m0, s0), seconds(h1, m1, s1), rest))
    return rows


@pytest.mark.parametrize("template", LEGACY_TEMPLATES)
def test_without_the_new_kwargs_the_output_is_unchanged(tmp_path, template):
    legacy = build(tmp_path, template)
    assert build(tmp_path, template, time_map=None, censor_spans=None) == legacy
    assert build(tmp_path, template, censor_spans=[]) == legacy
    assert build(tmp_path, template, time_map=lambda t: t) == legacy


@pytest.mark.parametrize("template", LEGACY_TEMPLATES)
def test_time_map_shifts_every_event(tmp_path, template):
    legacy = events(build(tmp_path, template))
    shifted = events(build(tmp_path, template, time_map=lambda t: t + 1.5))
    assert len(shifted) == len(legacy) > len(WORDS) // 4
    styles = {rest.split(",", 2)[0] for _, _, _, rest in legacy}
    assert {"Default", "Hook", "Point"} <= styles                 # captions, hook title and point labels
    for (layer, start, end, rest), (layer2, start2, end2, rest2) in zip(legacy, shifted):
        assert (layer2, rest2) == (layer, rest)                   # only the times move
        assert start2 == pytest.approx(start + 1.5, abs=0.011)
        assert end2 == pytest.approx(end + 1.5, abs=0.011)


def test_time_map_with_a_real_warp_stretches_events_across_a_freeze(tmp_path):
    warp = build_warp([FreezeCue(id="f1", start=2.0, end=2.0, hold=1.0),
                       SpeedCue(id="s1", start=5.0, end=6.0, rate=2.0)], 8.0)
    assert warp.nontrivial
    legacy = events(build(tmp_path))
    warped = events(build(tmp_path, time_map=warp.to_output))
    for (_, start, end, _), (_, start2, end2, _) in zip(legacy, warped):
        assert start2 == pytest.approx(warp.to_output(start), abs=0.011)
        assert end2 == pytest.approx(warp.to_output(end), abs=0.011)
    held = [(s, e, s2, e2) for (_, s, e, _), (_, s2, e2, _) in zip(legacy, warped) if s < 2.0 < e]
    assert held and all(e2 - s2 == pytest.approx(e - s + 1.0, abs=0.02) for s, e, s2, e2 in held)


def test_censor_spans_mask_overlapping_words_only(tmp_path):
    swear = next(word for word in WORDS if word["text"] == "fucking")
    neighbour = next(word for word in WORDS if word["text"] == "insane")
    text = build(tmp_path, "minimal", censor_spans=[(swear["start"] + 0.05, swear["end"] - 0.05),
                                                    (neighbour["end"] - 0.02, neighbour["end"] + 0.2)])
    body = "\n".join(rest for _, _, _, rest in events(text))
    assert "fucking" not in body.lower()
    assert "f******" in body.lower()
    assert "insane" in body.lower()                                # 20 ms overlap: below the 30 ms rule
    assert "absolutely" in body.lower()


def test_censor_spans_leave_the_hook_title_alone(tmp_path):
    text = build(tmp_path, "minimal", censor_spans=[(0.0, 0.3)])
    hook = [rest for _, _, _, rest in events(text) if rest.startswith("Hook,")]
    assert hook and "walked" in hook[0].lower() and "room" in hook[0].lower()


def test_censor_and_time_map_compose_in_edit_time(tmp_path):
    """Spans are edit time (where the bleep cue is); the map moves the censored events afterwards."""
    swear = next(word for word in WORDS if word["text"] == "fucking")
    text = build(tmp_path, "minimal", censor_spans=[(swear["start"], swear["end"])], time_map=lambda t: t + 10)
    body = "\n".join(rest for _, _, _, rest in events(text))
    assert "fucking" not in body.lower() and "f******" in body.lower()
    assert min(start for _, start, _, _ in events(text)) >= 10.0


def _inline_hook_end(output_duration: float) -> float:
    # The expression captions.build_hook_title_ass computed inline before the helper existed.
    return min(HOOK_TITLE_SECONDS, max(HOOK_TITLE_MIN_SECONDS, output_duration - 0.25))


@pytest.mark.parametrize("duration", [
    0.0, 0.5, 1.0, 1.5, 1.6, 1.75, 1.76, 2.0, 2.5, 3.0, 3.99, 4.0, 4.24, 4.25, 4.26, 5.0, 8.0, 15.0, 60.0, 180.0,
])
def test_hook_title_window_equals_the_old_inline_value(duration):
    assert hook_title_window(duration) == _inline_hook_end(duration)


@pytest.mark.parametrize("duration", [1.6, 2.0, 3.3, 4.25, 8.0, 60.0])
def test_the_burned_hook_title_ends_at_hook_title_window(duration):
    _, hook = captions.build_hook_title_ass("Title", {}, 1080, 1920, duration, "Arial", 62)
    assert f",{captions.ass_timestamp(hook_title_window(duration))},Hook," in hook[0]
    assert hook[0].startswith(f"Dialogue: 1,{captions.ass_timestamp(0.12)},")
