#!/usr/bin/env python3
"""Generate the cross-language dynamic-edit fixtures (G2) and the JSON Schemas from the Python implementation.

Writes ``shared/fx/fixtures/{rng,warp,mapping,duck,censor,geometry,groups,rows}/<case>.json`` and
``shared/fx/fx_cue.schema.json`` / ``shared/fx/fx_scene.schema.json`` at the repo root. pytest
(``tests/unit/fx/test_fixtures_parity.py``) and vitest (``frontend/src/lib/fx/core/parity.test.ts``) assert both
implementations reproduce them (floats within 1e-6, rng bit-exact).

Every fixture file is one case: ``{"family", "case", "fn", "input", "expected"}`` (``fn`` is the Python name).

The fixtures pin behaviour, so changing one is a decision: the script refuses to overwrite a file whose content
would change unless ``--accept`` is given. ``--check`` only reports differences (exit 1 when any).

    cd backend && .venv/bin/python scripts/fx/gen_fixtures.py [--accept | --check] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from pydantic import TypeAdapter  # noqa: E402

from src.edit_fx import constants  # noqa: E402
from src.edit_fx.caption_groups import grouped_words, phrase_groups  # noqa: E402
from src.edit_fx.caption_rows import group_rows, mapped_caption_rows  # noqa: E402
from src.edit_fx.censor import censor_text, censored_words  # noqa: E402
from src.edit_fx.contracts import CaptionRow, FxCue, FxScene  # noqa: E402
from src.edit_fx.duck import duck_envelope, envelope_db_at, speech_spans  # noqa: E402
from src.edit_fx.mapping import mapped_cues  # noqa: E402
from src.edit_fx.rng import fnv1a32, hash01, mulberry32  # noqa: E402
from src.edit_fx.warp import build_warp  # noqa: E402

DEFAULT_OUT = BACKEND.parent / "shared" / "fx"
FAMILIES = ("rng", "warp", "mapping", "duck", "censor", "geometry", "groups", "rows")
CUE = TypeAdapter(FxCue)


def cue(kind: str, cue_id: str, start: float, end: float | None = None, **fields: Any) -> dict:
    """A full cue JSON (defaults included), validated by the contract."""
    data = {"kind": kind, "id": cue_id, "start": start, "end": start if end is None else end, **fields}
    return CUE.dump_python(CUE.validate_python(data), mode="json")


def case(family: str, name: str, fn: str, inp: dict, expected: Any) -> tuple[str, dict]:
    return f"fixtures/{family}/{name}.json", {
        "family": family, "case": name, "fn": fn, "input": inp, "expected": expected,
    }


# ----------------------------------------------------------------------------------------------------------- rng

def rng_cases() -> list[tuple[str, dict]]:
    out = []
    texts = {
        "fnv_empty": "", "fnv_a": "a", "fnv_version": "fx-v1", "fnv_unicode": "ünï", "fnv_emoji": "🔥💀",
        "fnv_cue_seed": "2961888143:b3:sfx", "fnv_plan_seed": "task-123|4|fx-v1", "fnv_long": "kinetic_stack" * 9,
    }
    for name, text in texts.items():
        out.append(case("rng", name, "fnv1a32", {"text": text}, fnv1a32(text)))
    for seed in (42, 0, 0xFFFFFFFF, 123456789, 2961888143):
        draw = mulberry32(seed)
        out.append(case("rng", f"mulberry32_{seed}", "mulberry32", {"seed": seed, "n": 6},
                        [draw() for _ in range(6)]))
    for seed, n in ((7, 3), (0, 0), (0xFFFFFFFF, 1000), (2961888143, 17), (4242, 299), (1, 0xFFFFFFFF)):
        out.append(case("rng", f"hash01_{seed}_{n}", "hash01", {"seed": seed, "n": n}, hash01(seed, n)))
    return out


# ---------------------------------------------------------------------------------------------------------- warp

def _warp_case(name: str, cues: list[dict], duration: float) -> tuple[str, dict]:
    warp = build_warp([CUE.validate_python(c) for c in cues], duration)
    probes_e = sorted({round(duration * k / 16, 6) for k in range(-1, 18)} | {c["start"] for c in cues}
                      | {c["end"] for c in cues})
    probes_o = sorted({round(warp.out_duration * k / 16, 6) for k in range(-1, 18)}
                      | {piece.o0 for piece in warp.pieces})
    expected = {
        "pieces": [{"e0": p.e0, "e1": p.e1, "o0": p.o0, "rate": p.rate, "hold": p.hold} for p in warp.pieces],
        "edit_duration": warp.edit_duration,
        "out_duration": warp.out_duration,
        "nontrivial": warp.nontrivial,
        "to_output": [warp.to_output(e) for e in probes_e],
        "to_edit": [warp.to_edit(o) for o in probes_o],
        "json": warp.to_json(),
    }
    return case("warp", name, "build_warp",
                {"cues": cues, "edit_duration": duration, "probes_e": probes_e, "probes_o": probes_o}, expected)


def warp_cases() -> list[tuple[str, dict]]:
    return [
        _warp_case("empty_plan", [], 10.0),
        _warp_case("zero_duration", [], 0.0),
        _warp_case("non_warp_cues_ignored", [cue("sfx", "b0.sfx", 1.0, sound="pop"),
                                              cue("zoom", "b0.zoom", 1.0, 2.0, mode="punch", scale=1.2)], 6.0),
        _warp_case("freeze_at_zero", [cue("freeze", "b0.freeze", 0.0, hold=0.5)], 5.0),
        _warp_case("freeze_at_end", [cue("freeze", "b9.freeze", 8.0, hold=1.0)], 8.0),
        _warp_case("freeze_mid_unaligned", [cue("freeze", "b2.freeze", 2.345, hold=0.71, label="WAIT")], 6.0),
        _warp_case("two_freezes", [cue("freeze", "b1.freeze", 1.0, hold=0.4),
                                   cue("freeze", "b3.freeze", 3.5, hold=0.6)], 6.0),
        _warp_case("duplicate_freeze_instant", [cue("freeze", "a.freeze", 2.0, hold=0.5),
                                                cue("freeze", "b.freeze", 2.01, hold=1.0)], 4.0),
        _warp_case("speed_up", [cue("speed", "b1.speed", 2.0, 4.0, rate=2.0)], 8.0),
        _warp_case("slow_motion_tail", [cue("speed", "b4.slowmo", 3.0, 3.6, rate=0.5)], 6.0),
        _warp_case("speech_safe_ramp_fractional", [cue("speed", "b2.ramp", 1.0, 2.0, rate=1.3)], 4.0),
        _warp_case("overlapping_speed_earlier_wins", [cue("speed", "a.speed", 1.0, 3.0, rate=2.0),
                                                      cue("speed", "b.speed", 2.0, 4.0, rate=0.5)], 6.0),
        _warp_case("freeze_inside_speed_dropped", [cue("speed", "a.speed", 1.0, 3.0, rate=2.0),
                                                   cue("freeze", "b.freeze", 2.0, hold=0.5)], 5.0),
        _warp_case("freeze_at_speed_edges", [cue("speed", "a.speed", 1.0, 2.0, rate=1.5),
                                             cue("freeze", "b.freeze", 1.0, hold=0.3),
                                             cue("freeze", "c.freeze", 2.0, hold=0.3)], 4.0),
        _warp_case("speed_clipped_past_end", [cue("speed", "a.speed", 4.5, 9.0, rate=2.0)], 5.0),
        _warp_case("unaligned_duration", [cue("freeze", "a.freeze", 1.0, hold=0.5)], 10.01),
        _warp_case("rate_one_is_noop", [cue("speed", "a.speed", 1.0, 2.0, rate=1.0)], 3.0),
        _warp_case("edges_quantised", [cue("speed", "a.speed", 0.512, 1.987, rate=4.0)], 3.0),
    ]


# ------------------------------------------------------------------------------------------------------- mapping

def _mapping_case(name: str, segments: list, cues: list[dict], min_piece: float = 0.04) -> tuple[str, dict]:
    mapped = mapped_cues([tuple(s) for s in segments], [CUE.validate_python(c) for c in cues], min_piece=min_piece)
    return case("mapping", name, "mapped_cues", {"segments": segments, "cues": cues, "min_piece": min_piece},
                [CUE.dump_python(c, mode="json") for c in mapped])


def mapping_cases() -> list[tuple[str, dict]]:
    flash = cue("flash", "b0.flash", 1.0, 1.2)
    accent = cue("accent", "b1.accent", 1.0, 4.0, variant="circle", text="huge")
    pop = cue("sfx", "b1.sfx", 1.0, sound="pop")
    return [
        _mapping_case("generation_identity", [["clip", 0.0, 6.0]], [flash, accent, pop]),
        _mapping_case("empty_cues", [["s0", 0.0, 6.0]], []),
        _mapping_case("no_segments", [], [flash, pop]),
        _mapping_case("span_follows_cut", [["s0", 0.0, 2.0], ["s1", 3.0, 6.0]], [accent]),
        _mapping_case("reordered_segments", [["s0", 3.0, 6.0], ["s1", 0.0, 3.0]],
                      [accent, pop, cue("sfx", "b4.sfx", 4.0, sound="whoosh")]),
        _mapping_case("repeated_segment", [["s0", 3.0, 6.0], ["s1", 0.0, 3.0], ["s2", 0.0, 3.0]],
                      [cue("shake", "b1.shake", 1.0, 2.0, amp=12.0), pop]),
        _mapping_case("cut_out_cue_disappears", [["s0", 0.0, 2.0], ["s1", 4.0, 6.0]],
                      [cue("glitch", "b2.glitch", 2.5, 3.5, amount=0.6), cue("sfx", "b2.sfx", 3.0, sound="glitch")]),
        _mapping_case("short_piece_dropped", [["s0", 0.0, 2.0], ["s1", 4.0, 6.0]],
                      [cue("desaturate", "b3.desat", 1.98, 4.5, amount=0.8)]),
        _mapping_case("instant_lead_before_segment", [["s0", 3.0, 6.0]],
                      [cue("sfx", "b0.sfx", 2.97, sound="whoosh"), cue("sfx", "b1.sfx", 2.9, sound="pop")]),
        _mapping_case("instant_at_segment_end_noncontiguous", [["s0", 0.0, 2.0], ["s1", 4.0, 6.0]],
                      [cue("sfx", "b0.sfx", 2.0, sound="impact"), cue("freeze", "b0.freeze", 2.0, hold=0.5)]),
        _mapping_case("instant_at_segment_end_contiguous_next", [["s0", 0.0, 2.0], ["s1", 2.0, 4.0]],
                      [cue("sfx", "b0.sfx", 2.0, sound="impact")]),
        _mapping_case("instant_near_small_gap_once", [["s0", 0.0, 5.0], ["s1", 5.03, 10.0]],
                      [cue("sfx", "b0.sfx", 5.0, sound="impact"), cue("sfx", "b1.sfx", 4.98, sound="pop"),
                       cue("freeze", "b1.freeze", 4.99, hold=0.5), cue("sfx", "b2.sfx", 5.01, sound="whoosh")]),
        _mapping_case("instant_at_timeline_end", [["s0", 1.0, 3.0]],
                      [cue("transition", "g.transition", 3.0, style="whip")]),
        _mapping_case("long_id_repeat_suffix", [["s0", 0.0, 2.0], ["s1", 0.0, 2.0]],
                      [cue("flash", "x" * 32, 0.5, 0.7)]),
        _mapping_case("custom_min_piece", [["s0", 0.0, 2.0], ["s1", 4.0, 6.0]],
                      [cue("flash", "b0.flash", 1.9, 4.2)], min_piece=0.25),
    ]


# ---------------------------------------------------------------------------------------------------------- duck

DUCK_DEFAULTS = {"gain_db": -20.0, "duck_db": -12.0, "attack": 0.08, "release": 0.35, "fade_in": 0.5,
                 "fade_out": 0.8, "out_duration": 10.0, "floor_db": -60.0}


def _duck_case(name: str, words: list, merge_gap: float = 0.35, **params: float) -> tuple[str, dict]:
    p = {**DUCK_DEFAULTS, **params}
    spans = speech_spans([tuple(w) for w in words], merge_gap)
    points = duck_envelope(spans, **p)
    probes = sorted({round(p["out_duration"] * k / 20, 6) for k in range(-1, 22)} | {t for t, _ in points})
    return case("duck", name, "duck_envelope",
                {"words_final": words, "merge_gap": merge_gap, "params": p, "probes": probes},
                {"spans": [list(s) for s in spans], "points": [list(pt) for pt in points],
                 "at": [envelope_db_at(points, t) for t in probes]})


def duck_cases() -> list[tuple[str, dict]]:
    return [
        _duck_case("no_speech", []),
        _duck_case("single_phrase", [[2.0, 2.4], [2.5, 3.0], [3.1, 3.6]]),
        _duck_case("speech_at_zero", [[0.0, 1.2]]),
        _duck_case("speech_at_end", [[8.8, 10.0]]),
        _duck_case("close_phrases_join", [[1.0, 2.0], [2.3, 3.0]], merge_gap=0.1),
        _duck_case("far_phrases", [[1.0, 2.0], [5.0, 6.0]]),
        _duck_case("fades_scaled_to_fit", [[0.2, 0.4]], out_duration=1.0, fade_in=1.0, fade_out=1.0),
        _duck_case("no_fades", [[3.0, 4.0]], fade_in=0.0, fade_out=0.0),
        _duck_case("zero_duration", [[0.0, 1.0]], out_duration=0.0),
        _duck_case("overlapping_words", [[1.0, 2.0], [1.5, 2.5], [2.4, 2.6]]),
        _duck_case("speech_everywhere", [[0.0, 10.0]]),
        _duck_case("deep_duck_floor_clamp", [[4.0, 5.0]], gain_db=-40.0, duck_db=-30.0),
        _duck_case("unsorted_words_and_empty", [[6.0, 6.5], [1.0, 1.4], [3.0, 2.9], [5.0, 5.0]]),
        _duck_case("span_past_end", [[9.5, 12.0]]),
    ]


# -------------------------------------------------------------------------------------------------------- censor

def censor_cases() -> list[tuple[str, dict]]:
    texts = {
        "plain": "fuck", "capital_punct": "Fuck!", "apostrophe": "shit's", "german_eszett": "Scheiße",
        "cyrillic": "блядь", "spanish_hyphen": "co-ño", "already_starred": "f*ck", "digits_first": "123abc",
        "empty": "", "spaces": "  ", "accented_caps": "ÉCOLE", "cjk": "你好", "emoji_prefix": "💩shit",
        "quoted": "'damn'",
    }
    out = [case("censor", f"text_{name}", "censor_text", {"text": text}, censor_text(text))
           for name, text in texts.items()]
    words = [
        {"id": "w0", "start": 0.0, "end": 0.4, "text": "what"},
        {"id": "w1", "start": 0.4, "end": 0.8, "text": "the"},
        {"id": "w2", "start": 0.8, "end": 1.2, "text": "fuck,"},
        {"id": "w3", "start": 1.2, "end": 1.22, "text": "uh"},
        {"id": "w4", "start": 1.3, "end": 1.6, "text": "Scheiße"},
    ]
    for name, spans in {
        "words_exact_span": [[0.8, 1.2]],
        "words_short_overlap_kept": [[1.18, 1.21]],
        "words_overlap_30ms": [[0.37, 0.43]],
        "words_short_word_fully_covered": [[1.19, 1.25]],
        "words_no_spans": [],
        "words_two_spans": [[0.8, 1.2], [1.3, 1.6]],
    }.items():
        out.append(case("censor", name, "censored_words", {"words": words, "spans": spans},
                        censored_words(words, [tuple(s) for s in spans])))
    return out


# ------------------------------------------------------------------------------------------------------ geometry

def geometry_cases() -> list[tuple[str, dict]]:
    sizes = [(1080, 1920), (1080, 1080), (1920, 1080), (720, 1280), (1280, 720), (2160, 3840), (3840, 2160),
             (608, 1080), (1080, 1350), (1350, 1080), (540, 960), (1440, 2560)]
    out = []
    for w, h in sizes:
        x, y, rw, rh = constants.reaction_rect(w, h)
        out.append(case("geometry", f"size_{w}x{h}", "geometry", {"width": w, "height": h}, {
            "unit": constants.unit(w, h),
            "reaction_rect": {"x": x, "y": y, "w": rw, "h": rh},
            "caption_safe": constants.caption_safe(w, h),
            "hook_band": constants.HOOK_BAND,
        }))
    return out


# -------------------------------------------------------------------------------------------------------- groups

def _words(spec: str, start: float = 0.2, gap: float = 0.08, run: str | None = None) -> list[dict]:
    """``"so|0.3| this is"`` → timed words; ``|x|`` sets the pause before the next word."""
    out: list[dict] = []
    t, pending = start, None
    for token in spec.split():
        if token.startswith("|") and token.endswith("|"):
            pending = float(token.strip("|"))
            continue
        if out:
            t = out[-1]["end"] + (pending if pending is not None else gap)
        pending = None
        length = round(0.12 + 0.03 * len(token), 3)
        word = {"start": round(t, 3), "end": round(t + length, 3), "text": token}
        if run is not None:
            word["run"] = run
        out.append(word)
    return out


def _groups_case(name: str, words: list[dict], max_words: int) -> tuple[str, dict]:
    groups = phrase_groups(words, max_words)
    order = grouped_words(words)
    return case("groups", name, "phrase_groups", {"words": words, "max_words": max_words}, [
        {"first": g.first, "last": g.last, "start": g.start, "end": g.end,
         "text": " ".join(str(w["text"]) for w in order[g.first:g.last + 1])} for g in groups])


def groups_cases() -> list[tuple[str, dict]]:
    sentence = "So here is the thing about money nobody tells you, and it changes everything."
    a = _words("this is part one", run="s1")
    b = _words("and this part two", start=a[-1]["end"] + 0.05, run="s2")
    return [
        _groups_case("empty", [], 3),
        _groups_case("one_word", _words("Hello."), 3),
        _groups_case("max_three", _words(sentence), 3),
        _groups_case("max_four", _words(sentence), 4),
        _groups_case("max_one", _words("one two three four"), 1),
        _groups_case("pause_breaks", _words("we went |0.25| to the |0.4| market today"), 4),
        _groups_case("pause_just_under", _words("we went |0.24| to the market"), 4),
        _groups_case("conjunction_after_two", _words("I tried it but it failed so I quit"), 4),
        _groups_case("conjunction_needs_two", _words("but why so and then"), 4),
        _groups_case("clause_punctuation", _words("Wait, what? Really! Yes; no: maybe…"), 4),
        _groups_case("closing_quote", _words('He said "stop." then left'), 4),
        _groups_case("blank_words_dropped", _words("one two") + [{"start": 1.5, "end": 1.6, "text": "  "}]
                     + _words("three four", start=1.7), 3),
        _groups_case("unsorted_input", list(reversed(_words("alpha beta gamma delta epsilon"))), 2),
        _groups_case("runs_break_groups", a + b, 8),
        _groups_case("bridge_and_min_hold", _words("a |0.5| b |0.1| c"), 1),
        _groups_case("long_gap_last_group", _words("first group here |2.0| last"), 3),
        _groups_case("max_clamped_high", _words(sentence), 99),
    ]


# ---------------------------------------------------------------------------------------------------------- rows

def _rows(*spec: tuple[float, float, float]) -> list[dict]:
    return [CaptionRow(start=a, end=b, y=y).model_dump(mode="json") for a, b, y in spec]


def _group_rows_case(name: str, words: list[dict], max_words: int, rows: list[dict], y: float = 0.5):
    """Look caption groups ended by the rows' cuts, each on its row (fx-core ``layoutLookCaptions``)."""
    parsed = [CaptionRow.model_validate(r) for r in rows]
    return case("rows", name, "group_rows", {"words": words, "max_words": max_words, "rows": rows, "y": y},
                group_rows(words, max_words, parsed, y))


def _mapped_rows_case(name: str, segments: list, rows: list[dict]):
    """S rows through play-order segments onto E (fx-core ``mappedCaptionRows``)."""
    parsed = [CaptionRow.model_validate(r) for r in rows]
    return case("rows", name, "mapped_caption_rows", {"segments": segments, "rows": rows},
                [r.model_dump(mode="json") for r in mapped_caption_rows([tuple(s) for s in segments], parsed)])


def rows_cases() -> list[tuple[str, dict]]:
    talk = "So here is the thing about money nobody tells you and it changes everything for you"
    words = _words(talk)
    starts = [g.start for g in phrase_groups(words, 3)]
    third = starts[2]                                         # "money nobody tells" starts here
    mid = round((words[4]["start"] + words[5]["start"]) / 2, 3)   # between "thing" and "about" (group 2)
    shots = _rows((0.0, 1.0, 0.5), (1.0, 2.0, 0.65), (2.0, 3.0, 0.76), (3.0, 30.0, 0.5))
    paused = _words("we went |0.25| to the |0.9| market today")
    return [
        _group_rows_case("no_rows_keeps_style_y", words, 3, [], 0.5),
        _group_rows_case("cut_at_a_group_start", words, 3, _rows((0.0, third, 0.5), (third, 30.0, 0.65))),
        _group_rows_case("first_word_just_before_cut_opens_at_cut", words, 3,
                         _rows((0.0, round(third + 0.1, 3), 0.5), (round(third + 0.1, 3), 30.0, 0.65))),
        _group_rows_case("cut_splits_a_group", words, 3, _rows((0.0, mid, 0.5), (mid, 30.0, 0.65))),
        _group_rows_case("word_within_lead_moves_to_new_group", words, 3,
                         _rows((0.0, round(words[5]["start"] + 0.1, 3), 0.65),
                               (round(words[5]["start"] + 0.1, 3), 30.0, 0.5))),
        _group_rows_case("old_group_leaves_at_the_cut", paused, 4,
                         _rows((0.0, round(paused[3]["end"] - 0.02, 3), 0.5),
                               (round(paused[3]["end"] - 0.02, 3), 30.0, 0.76))),
        _group_rows_case("cut_in_a_pause_after_the_group", paused, 4,
                         _rows((0.0, round(paused[3]["end"] + 0.5, 3), 0.5),
                               (round(paused[3]["end"] + 0.5, 3), 30.0, 0.76))),
        _group_rows_case("alternating_shots", words, 3, shots),
        _group_rows_case("subtle_groups_of_four", words, 4, shots),
        _group_rows_case("same_height_rows_do_not_cut", words, 3, _rows((0.0, mid, 0.65), (mid, 30.0, 0.65))),
        _group_rows_case("before_first_row_takes_first", words, 3, _rows((1.0, 30.0, 0.65))),
        _group_rows_case("past_last_row_keeps_last", words, 3, _rows((0.0, 1.0, 0.5), (1.0, 2.0, 0.76))),
        _group_rows_case("empty_words", [], 3, _rows((0.0, 1.0, 0.5), (1.0, 9.0, 0.65))),
        _mapped_rows_case("identity", [["clip", 0.0, 6.0]], _rows((0.0, 2.0, 0.5), (2.0, 6.0, 0.65))),
        _mapped_rows_case("trimmed_head_and_tail", [["mid", 1.0, 5.0]],
                          _rows((0.0, 2.0, 0.5), (2.0, 4.5, 0.65), (4.5, 6.0, 0.76))),
        _mapped_rows_case("reordered_segments", [["late", 3.0, 6.0], ["early", 0.0, 2.5]],
                          _rows((0.0, 2.0, 0.5), (2.0, 6.0, 0.65))),
        _mapped_rows_case("repeated_segment", [["a", 0.0, 3.0], ["again", 1.0, 2.5]],
                          _rows((0.0, 2.0, 0.5), (2.0, 6.0, 0.65))),
        _mapped_rows_case("cut_joins_equal_rows", [["a", 0.0, 1.5], ["b", 4.0, 6.0]],
                          _rows((0.0, 2.0, 0.65), (2.0, 3.5, 0.5), (3.5, 6.0, 0.65))),
        _mapped_rows_case("no_rows", [["a", 0.0, 6.0]], []),
    ]


GENERATORS: dict[str, Callable[[], list[tuple[str, dict]]]] = {
    "rng": rng_cases, "warp": warp_cases, "mapping": mapping_cases, "duck": duck_cases, "censor": censor_cases,
    "geometry": geometry_cases, "groups": groups_cases, "rows": rows_cases,
}


def schemas() -> dict[str, dict]:
    cue_schema = CUE.json_schema()
    cue_schema["$id"] = "https://supoclip.com/schemas/fx/fx_cue.schema.json"
    scene_schema = FxScene.model_json_schema()
    scene_schema["$id"] = "https://supoclip.com/schemas/fx/fx_scene.schema.json"
    return {"fx_cue.schema.json": cue_schema, "fx_scene.schema.json": scene_schema}


def render(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def build_outputs() -> dict[str, str]:
    """Relative path (under ``shared/fx``) → file content."""
    outputs: dict[str, str] = {}
    for family in FAMILIES:
        for rel, payload in GENERATORS[family]():
            if rel in outputs:
                raise RuntimeError(f"duplicate fixture {rel}")
            outputs[rel] = render(payload)
    for rel, payload in schemas().items():
        outputs[rel] = render(payload)
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--accept", action="store_true", help="overwrite fixtures whose content changes")
    parser.add_argument("--check", action="store_true", help="only report differences; exit 1 when any")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output root (default: shared/fx)")
    args = parser.parse_args(argv)

    outputs = build_outputs()
    changed = [rel for rel, content in outputs.items()
               if (args.out / rel).exists() and (args.out / rel).read_text(encoding="utf-8") != content]
    missing = [rel for rel in outputs if not (args.out / rel).exists()]
    expected_dirs = {args.out / "fixtures" / family for family in FAMILIES}
    stale = sorted(
        str(path.relative_to(args.out))
        for folder in expected_dirs if folder.is_dir()
        for path in folder.glob("*.json")
        if str(path.relative_to(args.out)) not in outputs
    )

    if args.check:
        for rel in changed + missing + stale:
            print(f"differs: {rel}")
        return 1 if changed or missing or stale else 0
    if (changed or stale) and not args.accept:
        for rel in changed:
            print(f"would change: {rel}", file=sys.stderr)
        for rel in stale:
            print(f"would remove: {rel}", file=sys.stderr)
        print("Refusing to overwrite pinned fixtures; re-run with --accept if the change is intended.", file=sys.stderr)
        return 1
    for rel in changed + missing:
        path = args.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(outputs[rel], encoding="utf-8")
    for rel in stale:
        (args.out / rel).unlink()
    print(f"{len(outputs)} files: {len(missing)} new, {len(changed)} updated, {len(stale)} removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
