#!/usr/bin/env python3
"""Generate the resolver golden fixtures (WP2b) in ``backend/tests/fixtures/fx_resolver/``.

Three transcripts, tagged by hand (no LLM, no dependency on the beat validator):

- ``talking_head``: money advice with numbers, currency, swears and a punchline;
- ``story``: a story that builds to a payoff, run through the REAL default cleanup
  (``media.timeline.build_clip_keep_ranges`` with ``cut_long_pauses`` + ``remove_filler_words``, the new-task
  defaults), so its words, cuts and passage breaks are exactly what generation would see;
- ``no_timing``: a clip without word timings (``timing == "none"``): only global FX.

Each transcript writes ``<name>.input.json`` (the ``ResolveInput`` fields + the plan seed) and ``<name>.golden.json``:
for every template (10 FX + ``default``) x intensity, the sha256 of the canonical plan JSON and a readable cue list,
so a failing golden shows WHAT changed. Goldens pin behaviour: the script refuses to overwrite a file whose content
would change unless ``--accept`` is given (bump ``EDIT_FX_VERSION`` when resolver output changes on purpose).
``--check`` only reports differences (exit 1 when any).

    cd backend && .venv/bin/python scripts/fx/gen_resolver_goldens.py [--accept | --check]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

OUT_DIR = BACKEND / "tests" / "fixtures" / "fx_resolver"

# Built-in manifest shape (§12): variant counts per sound (= manifest.json, checked by test_fx_assets_manifest),
# and every built-in bed of each mood with its loop length (from manifest.json).
SFX_VARIANTS = {"whoosh": 6, "impact": 7, "pop": 6, "riser": 8, "record_scratch": 6, "boom": 6, "cash_register": 5,
                "ticker": 6, "slap": 6, "stamp": 6, "glitch": 6, "tape_stop": 6}
_MUSIC = json.loads((BACKEND / "src" / "fx_assets" / "manifest.json").read_text(encoding="utf-8"))["music"]
BEDS: dict[str, list[str]] = {}
for _entry in _MUSIC:
    BEDS.setdefault(_entry["mood"], []).append(_entry["id"])
BED_LENGTHS = {entry["id"]: float(entry["loop_seconds"]) for entry in _MUSIC}
DEFAULT_CLEANUP = {"cut_long_pauses": True, "pause_threshold_ms": 1200, "remove_filler_words": True,
                   "filtered_words": []}
PAUSE = re.compile(r"^\|(\d+(?:\.\d+)?)\|$")

# ----------------------------------------------------------------------------- transcripts
# Raw speech with explicit pauses `|seconds|`. Word length 0.2 s + 0.035 s per character; 0.08 s between words and
# 0.35 s after a sentence end unless a pause marker says otherwise. Fillers ("um", "uh") are removed by cleanup.

TALKING_HEAD = """
Most people will never make a hundred grand. Not because they're lazy. Because nobody told them this. |0.6|
I went from $40,000 a year to $2.5M in three years. um And the shit part? I almost quit twice. |1.5|
Here's the trick. Stop trading hours for money. |0.9| Build one thing that sells while you sleep. |0.7| That's it! |0.8|
Honestly it sounds stupid, but it fucking works. |1.6| Do this for 90 days and thank me later.
"""

STORY = """
I was sixteen when my dad left. |1.3| No note, no call, nothing. |0.9| For ten years I told myself I didn't care. |0.5|
um But every birthday I checked the mailbox. |3.6| Then last month a letter showed up. My hands were shaking. |0.7|
I opened it, and it was a photo. |1.2| Me, at six, on his shoulders. |0.6| On the back he wrote: I never stopped
watching. |1.8| I called him that night. We talked for four hours. |0.9| Turns out he'd been sober for nine years.
|1.4| He just didn't think he deserved to come back. |0.9| So I told him the truth. You don't get to decide that.
|0.4| Dad. |1.0| He's coming to my wedding in June.
"""

# Beats tagged on the CLEANED words: (kind, phrase, strength, tone, extras). The phrase is matched in order.
TALKING_HEAD_BEATS = [
    ("hook", "Most people will never make a hundred grand.", 3, "shocked", {}),
    ("number", "a hundred grand.", 2, "hype", {"value": 100000.0, "prefix": "$"}),
    ("emphasis", "lazy.", 1, "neutral", {}),
    ("build", "Because nobody told them this.", 2, "suspense", {}),
    ("number", "$40,000", 2, "neutral", {"value": 40000.0, "prefix": "$"}),
    ("number", "$2.5M", 3, "hype", {"value": 2.5, "prefix": "$", "suffix": "M", "emoji": "💰"}),
    ("number", "three years.", 1, "neutral", {"value": 3.0}),
    ("swear", "shit", 2, "neutral", {}),
    ("punchline", "I almost quit twice.", 3, "funny", {"label": "ALMOST"}),
    ("emphasis", "Stop trading hours for money.", 2, "angry", {}),
    ("payoff", "Build one thing that sells while you sleep.", 3, "hype", {"label": "THE TRICK"}),
    ("emphasis", "That's it!", 2, "smug", {}),
    ("emphasis", "stupid,", 1, "awkward", {}),
    ("swear", "fucking", 2, "neutral", {}),
    ("emphasis", "works.", 2, "hype", {}),
    ("number", "90 days", 2, "neutral", {"value": 90.0}),
]
TALKING_HEAD_REACTIONS = [("twice.", "phew close call")]

STORY_BEATS = [
    ("hook", "I was sixteen when my dad left.", 3, "sad", {}),
    ("number", "sixteen", 1, "neutral", {"value": 16.0}),
    ("emphasis", "nothing.", 2, "sad", {}),
    ("number", "ten years", 1, "neutral", {"value": 10.0}),
    ("build", "Then last month a letter showed up.", 2, "suspense", {}),
    ("build", "I opened it, and it was a photo.", 3, "suspense", {}),
    ("payoff", "Me, at six, on his shoulders.", 3, "wholesome", {"label": "NO WAY"}),
    ("emotional_turn", "I never stopped watching.", 3, "sad", {}),
    ("number", "four hours.", 1, "neutral", {"value": 4.0}),
    ("emphasis", "sober", 2, "shocked", {}),
    ("punchline", "You don't get to decide that.", 3, "smug", {"label": "TOLD HIM"}),
    ("payoff", "He's coming to my wedding in June.", 3, "wholesome", {"label": "WEDDING"}),
]
STORY_REACTIONS = [("wedding", "happy tears")]


def raw_words(script: str, start: float) -> list[dict]:
    words: list[dict] = []
    t = start
    pending_gap: float | None = None
    for token in script.split():
        m = PAUSE.match(token)
        if m:
            pending_gap = float(m.group(1))
            continue
        if words:
            prev = words[-1]["text"]
            gap = pending_gap if pending_gap is not None else (0.35 if re.search(r"[.!?]$", prev) else 0.08)
            t = words[-1]["end"] + gap
        pending_gap = None
        length = 0.2 + 0.035 * len(token)
        words.append({"text": token, "start": round(t, 3), "end": round(t + length, 3), "confidence": 0.99})
    return words


def _norm(text: str) -> str:
    return re.sub(r"[^\w$%.,']", "", text.lower())


def words_hash(words: list[dict]) -> str:
    blob = json.dumps([(w["text"], round(w["start"], 2), round(w["end"], 2)) for w in words], ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def cleaned(raw: list[dict], clip_start: float, clip_end: float) -> dict[str, Any]:
    """Run the real default cleanup and project the words onto the clip (S) timeline, as generation does."""
    from src.media import ffmpeg as media_ffmpeg
    from src.media import timeline

    # Transcript caches store word times in integer milliseconds (AssemblyAI shape).
    transcript = {"words": [{"text": w["text"], "start": int(round(w["start"] * 1000)),
                             "end": int(round(w["end"] * 1000)), "confidence": w["confidence"]} for w in raw],
                  "language_code": "en"}
    original = timeline.load_cached_transcript_data
    timeline.load_cached_transcript_data = lambda _path: transcript
    try:
        keep = timeline.build_clip_keep_ranges(Path("/nonexistent/source.mp4"), clip_start, clip_end,
                                               DEFAULT_CLEANUP)
    finally:
        timeline.load_cached_transcript_data = original
    offsets = media_ffmpeg.output_offsets_for_ranges(keep)
    duration = media_ffmpeg.rendered_ranges_duration(keep)
    fades = media_ffmpeg.junction_fades_for_offsets(keep, offsets) if len(keep) > 1 else []
    cuts = [round(offsets[i] + fades[i - 1] / 2, 3) for i in range(1, len(keep))]
    breaks = [cuts[i - 1] for i in range(1, len(keep)) if keep[i][0] - keep[i - 1][1] >= 3.0]
    # Same projection as timeline.get_words_for_keep_ranges, keeping each word's A (source) times.
    words: list[dict] = []
    for (keep_start, keep_end), offset in zip(keep, offsets):
        for w in timeline.get_absolute_words_in_range(transcript, keep_start, keep_end):
            words.append({"id": f"w{len(words)}", "start": round(offset + (w["start"] - keep_start), 3),
                          "end": round(offset + (w["end"] - keep_start), 3), "text": w["text"],
                          "src_start": w["start"], "src_end": w["end"]})
    reference = timeline.get_words_for_keep_ranges(transcript, keep, offsets)
    assert [(round(r["start"], 3), r["text"]) for r in reference] == [(w["start"], w["text"]) for w in words]
    return {"words": words, "duration": round(duration, 3), "cuts": cuts, "passage_breaks": breaks,
            "keep_ranges": [list(r) for r in keep], "output_offsets": [round(o, 3) for o in offsets]}


def tag(words: list[dict], specs: list[tuple], reactions: list[tuple[str, str]], *, mood: str) -> dict:
    tagged = []
    cursor = 0
    for kind, phrase, strength, tone, extra in specs:
        needle = [_norm(t) for t in phrase.split()]
        for i in range(len(words)):
            if [_norm(w["text"]) for w in words[i:i + len(needle)]] == needle:
                break
        else:
            raise SystemExit(f"phrase not found after cleanup: {phrase!r}")
        span = words[i:i + len(needle)]
        cursor = max(cursor, i)
        tagged.append({
            "kind": kind, "start": span[0]["start"], "end": span[-1]["end"],
            "text": " ".join(w["text"] for w in span)[:200], "strength": strength, "tone": tone,
            "source": "llm", "src_start": span[0]["src_start"], "src_end": span[-1]["src_end"],
            "words": [re.sub(r"[^\w$%']", "", w["text"].lower()) for w in span][:8], **extra,
        })
    tagged.sort(key=lambda b: (b["start"], b["end"], b["kind"]))
    for n, beat in enumerate(tagged):
        beat["id"] = f"b{n}"
    hook = next((b for b in tagged if b["kind"] == "hook"), None)
    ideas = []
    for word_text, query in reactions:
        w = next(w for w in words if _norm(w["text"]) == _norm(word_text))
        ideas.append({"at": w["start"], "query": query})
    return {"version": "beats-v1", "words_hash": words_hash(words), "timeline_hash": "", "model": "fixture:hand",
            "source": "llm", "mood": mood, "sensitivity": "normal", "timing": "word",
            "hook_end": hook["end"] if hook else None, "beats": tagged, "reactions": ideas}


def hook_window(duration: float) -> list[float]:
    # media.common hook_title_window: min(4, max(HOOK_TITLE_MIN_SECONDS, dur - 0.25))
    return [0.12, round(min(4.0, max(1.5, duration - 0.25)), 3)]


def build_inputs() -> dict[str, dict]:
    th_raw = raw_words(TALKING_HEAD, 120.0)
    th = cleaned(th_raw, 119.9, th_raw[-1]["end"] + 0.4)
    story_raw = raw_words(STORY, 3600.0)
    st = cleaned(story_raw, 3599.8, story_raw[-1]["end"] + 0.6)
    common = {"sfx_variants": SFX_VARIANTS, "beds": BEDS,
              "bed_lengths": BED_LENGTHS, "reactions_available": True, "max_clip_seconds": 90.0,
              "broll_windows": [], "user_cues": [], "suppressed": []}
    talking_head = {
        "name": "talking_head", "seed": 2_816_310_017, **common, **th,
        "beats": tag(th["words"], TALKING_HEAD_BEATS, TALKING_HEAD_REACTIONS, mood="hype"),
        "hook_window": hook_window(th["duration"]),
    }
    talking_head["broll_windows"] = [[round(th["duration"] * 0.55, 3), round(th["duration"] * 0.55 + 2.4, 3)]]
    story = {
        "name": "story", "seed": 1_407_221_113, **common, **st,
        "beats": tag(st["words"], STORY_BEATS, STORY_REACTIONS, mood="dramatic"),
        "hook_window": hook_window(st["duration"]),
    }
    no_timing = {
        "name": "no_timing", "seed": 977_123_401, **common, "words": [], "duration": 30.0,
        "cuts": [9.8, 21.4], "passage_breaks": [21.4], "keep_ranges": [], "output_offsets": [],
        "beats": {"version": "beats-v1", "words_hash": words_hash([]), "timeline_hash": "", "model": "",
                  "source": "heuristic", "mood": "hype", "sensitivity": "normal", "timing": "none",
                  "hook_end": None, "beats": [], "reactions": []},
        "hook_window": hook_window(30.0),
    }
    return {"talking_head": talking_head, "story": story, "no_timing": no_timing}


# ----------------------------------------------------------------------------- goldens


def dump(obj: Any) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--accept", action="store_true", help="overwrite fixtures whose content changed")
    mode.add_argument("--check", action="store_true", help="only report differences")
    args = parser.parse_args()

    files: dict[Path, str] = {}
    for name, data in build_inputs().items():
        files[OUT_DIR / f"{name}.input.json"] = dump(data)
        from tests.fixtures.fx_resolver.builders import goldens_for

        files[OUT_DIR / f"{name}.golden.json"] = dump(goldens_for(data))

    changed = [p for p, text in files.items() if p.exists() and p.read_text() != text]
    missing = [p for p in files if not p.exists()]
    if args.check:
        for p in changed + missing:
            print(f"differs: {p.relative_to(BACKEND)}")
        return 1 if changed or missing else 0
    if changed and not args.accept:
        for p in changed:
            print(f"refusing to overwrite {p.relative_to(BACKEND)} (content changed); rerun with --accept")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for p, text in files.items():
        if not p.exists() or p.read_text() != text:
            p.write_text(text)
            print(f"wrote {p.relative_to(BACKEND)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
