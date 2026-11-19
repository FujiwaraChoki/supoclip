"""Resolver (§8) named tests: every §8.4 cell, the intrinsic-SFX table, every §8.5 rule, the §8.7 variants, goldens."""

from __future__ import annotations

import json
import math
import re

import pytest

from src.edit_fx.contracts import (
    MOODS,
    SOUND_IDS,
    BleepCue,
    EditStyle,
    FlashCue,
    SfxCue,
    SpeedCue,
    ZoomCue,
)
from src.edit_fx.resolver import MOOD_MUSIC_TRIM_DB, MUSIC_LEVELS, ResolveInput, resolve, sfx_bus_gain_db
from tests.fixtures.fx_resolver.builders import (
    BED_LENGTHS,
    BEDS,
    FX_TEMPLATES,
    INTENSITIES,
    TEMPLATES,
    beat,
    beats_of,
    describe,
    goldens_for,
    load_fixture,
    make_input,
    words_from,
    FIXTURES,
)

# A roomy ~45 s transcript: one beat under test sits mid-clip with no competition for budget.
SCRIPT = """
Okay so here is the thing about this whole story. |0.5| We went to the market on a sunday morning to buy some bread.
|0.5| It was raining and nobody really wanted to be outside that day. |0.5| Then my brother found an old wallet on the
ground. |0.7| Inside there was a note with a phone number and a name. |0.5| We called the number and a man answered
right away. |0.5| He said the wallet belonged to his father who passed last year. |0.5| He drove over and we gave it
back to him. |0.5| He cried and hugged us both for a long time. |0.6| Honestly it was the best day of that whole month.
"""
WORDS = words_from(SCRIPT)
DUR = round(WORDS[-1]["end"] + 0.8, 3)
SEED = 4242


def run(beats, *, template="default", intensity="bold", words=WORDS, seed=SEED, style=None, meta=None, **kw):
    eb = beats_of(beats, **(meta or {}))
    inp = make_input(words, eb, **({"duration": DUR} if words is WORDS and "duration" not in kw else {}), **kw)
    return resolve(inp, style or EditStyle(intensity=intensity), template, seed)


def cues(plan, kind=None, beat_id=None):
    return [c for c in plan.cues if (kind is None or c.kind == kind) and (beat_id is None or c.beat == beat_id)]


def sounds(plan, beat_id=None):
    return {c.sound for c in cues(plan, "sfx", beat_id)}


def one(plan, cue_id):
    found = [c for c in plan.cues if c.id == cue_id]
    assert len(found) == 1, (cue_id, [describe(c) for c in plan.cues])
    return found[0]


def has(plan, cue_id):
    return any(c.id == cue_id for c in plan.cues)


def mid(phrase, kind, **kw):
    return beat(WORDS, phrase, kind, **kw)


HOOK = "Okay so here is the thing about this whole story."


# ============================================================================= fx-v2: what is never automatic

BUILD = "We called the number and a man answered right away."


def payoff_scene(strength=3, duration=None):
    hook = mid(HOOK, "hook", strength=2)
    pay = mid("He drove over and we gave it back to him.", "payoff", strength=strength, label="NO WAY")
    return [hook, pay], ({"duration": duration} if duration else {})


ALL_KINDS_BEATS = [
    mid(HOOK, "hook", strength=3), mid("wallet", "emphasis", strength=3), mid("a phone number", "number", value=1.0),
    mid("hugged us both", "punchline", strength=3), mid(BUILD, "build", strength=3),
    mid("He said the wallet belonged", "emotional_turn", strength=3), mid("a long time.", "payoff", strength=3),
]


@pytest.mark.parametrize("intensity", ["subtle", "bold", "max"])
@pytest.mark.parametrize("template", ["default", "highlighter_swipe", "emoji_drop", "behind_head"])
def test_never_automatic_shake_hits_warp_risers_bursts_or_stickers(template, intensity):
    plan = run(ALL_KINDS_BEATS, template=template, intensity=intensity, cuts=(15.0, 30.0),
               passage_breaks=(15.0,))
    kinds = {c.kind for c in plan.cues}
    assert not kinds & {"shake", "flash", "glitch", "desaturate", "speed", "freeze", "sticker"}, kinds
    assert not {"riser", "tape_stop", "record_scratch", "cash_register", "stamp", "ticker", "slap"} & sounds(plan)
    assert not [c for c in cues(plan, "accent") if c.variant == "emoji_burst"]
    assert not [c for c in cues(plan, "zoom") if c.mode == "push"]
    transitions = cues(plan, "transition")
    assert all(c.style in ("flash", "light_leak") for c in transitions)


# ============================================================================= fx-v2: reframes (spec §3.5)


def zooms(plan, mode):
    return sorted((c for c in cues(plan, "zoom") if c.mode == mode), key=lambda c: c.start)


@pytest.mark.parametrize("intensity,lo,hi", [("subtle", 1.10, 1.15), ("bold", 1.12, 1.20), ("max", 1.20, 1.35)])
def test_reframes_hold_until_the_next_reframe_and_alternate(intensity, lo, hi):
    plan = run(ALL_KINDS_BEATS, intensity=intensity)
    punches = zooms(plan, "punch")
    assert punches
    for a, b in zip(punches, punches[1:]):
        assert a.end <= b.start + 1e-6                     # never stacked
        assert b.start - a.end >= 0.8 - 1e-6               # an "out" stretch at the base framing between them
    for p in punches:
        assert lo - 1e-6 <= p.scale <= hi + 1e-6
        assert p.end - p.start >= 0.4 - 1e-6               # a reframe, never a 0.5 s bounce
    rate = {"subtle": 1.2, "bold": 2.6, "max": 5.5}[intensity]
    events = len(punches) * 2
    assert events <= max(2, math.floor(rate * DUR / 10 + 0.5) + 1)


def test_reframes_reset_at_real_cuts_and_skip_broll():
    plan = run(ALL_KINDS_BEATS, intensity="max", cuts=(12.0, 24.0))
    for p in zooms(plan, "punch"):
        assert not any(p.start + 1e-6 < c < p.end - 1e-6 for c in (12.0, 24.0))
    window = (10.0, 20.0)
    plan = run(ALL_KINDS_BEATS, intensity="max", broll_windows=(window,))
    for z in cues(plan, "zoom"):
        assert z.end <= window[0] + 1e-6 or z.start >= window[1] - 1e-6


def test_nudges_are_11_frame_pushes_that_never_stack():
    plan = run(ALL_KINDS_BEATS, intensity="max")
    nudges = zooms(plan, "nudge")
    pulls = zooms(plan, "pull")
    assert nudges
    for n in nudges:
        assert 1.08 - 1e-6 <= n.scale <= 1.10 + 1e-6
        overlapping = [o for o in nudges if o is not n and o.start < n.end - 1e-6 and n.start < o.end - 1e-6]
        assert not overlapping
    for p in pulls:                                        # a pull eases the previous nudge back out over 11 frames
        assert p.end - p.start <= 11 / 30 + 1e-3
        assert any(abs(n.end - p.start) < 1e-6 and n.scale == p.scale for n in nudges)


def test_zooms_off_switch_and_style_only():
    assert not cues(run(ALL_KINDS_BEATS, style=EditStyle(intensity="max", zooms=False)), "zoom")
    assert not cues(run(ALL_KINDS_BEATS, template="highlighter_swipe", intensity="off"), "zoom")


# ============================================================================= fx-v2: sounds on visual events (spec §3.7)


@pytest.mark.parametrize("intensity", ["subtle", "bold", "max"])
def test_sfx_sit_on_reframes_one_frame_early_never_on_plain_talking(intensity):
    plan = run(ALL_KINDS_BEATS, intensity=intensity)
    punch_times = [p.start for p in zooms(plan, "punch")] + [p.end for p in zooms(plan, "punch")]
    hits = [c for c in cues(plan, "sfx") if c.id.startswith("e")]
    if intensity == "subtle":
        assert not cues(plan, "sfx")
        return
    assert hits and all(c.sound in ("impact", "boom") for c in hits)
    for c in hits:
        assert any(abs(c.start - (t - 1 / 30)) < 0.02 for t in punch_times), (c.start, punch_times)
    share = {"bold": 0.5, "max": 0.6}[intensity]
    assert len(hits) <= math.floor(share * len(punch_times) + 0.5) + 1


def test_flash_transition_whoosh_two_frames_early_and_transition_rates():
    cuts = (8.0, 16.0, 24.0, 32.0)
    assert not cues(run([], intensity="subtle", cuts=cuts, passage_breaks=cuts), "transition")
    bold = run([], intensity="bold", cuts=cuts, passage_breaks=())
    assert not cues(bold, "transition")                   # bold: passage breaks only
    bold = run([], intensity="bold", cuts=cuts, passage_breaks=(16.0,))
    assert [c.start for c in cues(bold, "transition")] == [16.0]
    wild = run([], intensity="max", cuts=cuts, passage_breaks=())
    assert 1 <= len(cues(wild, "transition")) <= max(1, math.floor(DUR / 20 + 0.5))
    for tr in cues(wild, "transition") + cues(bold, "transition"):
        if tr.style == "flash":
            whoosh = [c for c in cues(wild, "sfx") + cues(bold, "sfx") if c.id == f"{tr.id}.sfx"]
            assert whoosh and whoosh[0].start == pytest.approx(tr.start - 2 / 30, abs=0.02)


def test_sfx_bus_gain_row_and_cue_gains_stay_relative():
    assert [sfx_bus_gain_db(EditStyle(intensity=i), "normal") for i in INTENSITIES] == [-16.0, -20.0, -16.0, -10.0]
    assert sfx_bus_gain_db(EditStyle(intensity="max"), "serious") == -20.0
    plan = run(ALL_KINDS_BEATS, intensity="max")
    assert all(c.gain_db <= 0 for c in cues(plan, "sfx"))       # per-sound trims only


# ============================================================================= fx-v2: look templates (spec §3.1-3.4)


def keywords(plan):
    return sorted((c for c in cues(plan, "accent") if c.variant == "highlight"), key=lambda c: c.start)


@pytest.mark.parametrize("intensity,rate,gap", [("subtle", 1.25, 4.0), ("bold", 4.0, 1.2), ("max", 5.5, 0.8)])
def test_keywords_one_per_phrase_group_at_the_look_rate(intensity, rate, gap):
    from src.edit_fx.caption_groups import LOOK_MAX_WORDS, phrase_groups

    plan = run(ALL_KINDS_BEATS, template="highlighter_swipe", intensity=intensity)
    kws = keywords(plan)
    assert kws and len(kws) <= max(1, math.floor(rate * DUR / 10 + 0.5))
    for a, b in zip(kws, kws[1:]):
        assert b.start - a.start >= gap - 1e-6
    groups = phrase_groups(WORDS, LOOK_MAX_WORDS[intensity])
    seen = set()
    for k in kws:
        g = next(i for i, grp in enumerate(groups) if grp.start - 1e-6 <= k.start <= WORDS[grp.last]["start"] + 1e-6)
        assert g not in seen
        seen.add(g)
        assert len(k.text.split()) == 1
        assert k.text.strip(".,!?").lower() not in ("so", "the", "basically", "really", "actually")


def test_keyword_prefers_the_beat_key_word_and_skips_fillers():
    plan = run([mid("wallet", "emphasis", strength=3)], template="highlighter_swipe", intensity="bold")
    assert any(k.text == "wallet" and k.beat == "b0" for k in keywords(plan))
    w = words_from("so basically I literally actually really think honestly. " * 6)
    plan = run([], words=w, template="highlighter_swipe", intensity="max", duration=round(w[-1]["end"] + 0.5, 3))
    assert all(k.text.lower().strip(".") not in ("basically", "literally", "actually", "honestly", "really")
               for k in keywords(plan))


def test_emoji_drop_emoji_hold_with_their_group_and_respect_rates():
    beats = [mid("wallet", "emphasis", strength=2, emoji="💰"), mid("a phone number", "number", value=1.0, emoji="📱"),
             mid("hugged us both", "punchline", strength=3, tone="funny"), mid("a long time.", "payoff",
                                                                               strength=3, tone="sad")]
    for intensity, rate in (("subtle", 0.5), ("bold", 1.0), ("max", 2.5)):
        plan = run(beats, template="emoji_drop", intensity=intensity)
        emoji = [c for c in cues(plan, "accent") if c.variant == "emoji"]
        assert 1 <= len(emoji) <= max(1, math.floor(rate * DUR / 10 + 0.5))
        for e in emoji:
            assert 0.5 - 1e-6 <= e.end - e.start <= 1.8 + 1e-6
            assert e.emoji != "😢"                               # never on a sad (vulnerable) moment
        pops = [c for c in cues(plan, "sfx") if c.sound == "pop"]
        assert len(pops) <= math.ceil(len(emoji) / 3)
    plan = run(beats, template="emoji_drop", intensity="bold")
    first = min((c for c in cues(plan, "accent") if c.variant == "emoji"), key=lambda c: c.start)
    assert first.emoji == "💰"
    key = next(w for w in WORDS if w["text"] == "wallet")
    assert first.start == pytest.approx(key["start"] - 2 / 30, abs=2e-3)


def test_behind_head_word_lands_on_a_reframe_and_hides_captions():
    plan = run(ALL_KINDS_BEATS, template="behind_head", intensity="bold")
    behind = [c for c in cues(plan, "accent") if c.variant == "behind"]
    assert 1 <= len(behind) <= max(1, math.floor(DUR / 20 + 0.5))
    punches = zooms(plan, "punch")
    for b in behind:
        assert len(b.text.split()) == 1
        assert 1.0 - 1e-6 <= b.end - b.start <= 2.0 + 1e-6
        assert any(abs(p.start - b.start) < 1e-3 or abs(p.end - b.start) < 1e-3 for p in punches)
        assert has(plan, f"{b.id}.sfx") and one(plan, f"{b.id}.sfx").sound == "boom"
        assert not [k for k in keywords(plan) if k.start < b.end and b.start < k.end]


def test_legacy_and_hidden_templates_get_no_look_cues():
    plan = run(ALL_KINDS_BEATS, template="default", intensity="max")
    assert not cues(plan, "accent")
    plan = run(ALL_KINDS_BEATS, template="slam", intensity="max")
    assert not [c for c in cues(plan, "accent") if c.id.endswith(".kw")]


# ============================================================================= intrinsic SFX of hidden templates


def test_intrinsic_slam_impact_boom_at_max():
    b = mid("hugged us both", "punchline", strength=3)
    assert one(run([b], template="slam", intensity="bold"), "b0.slam.sfx").sound == "impact"
    assert one(run([b], template="slam", intensity="max"), "b0.slam.sfx").sound == "boom"


def test_intrinsic_sfx_respect_sfx_toggle():
    plan = run([mid("wallet", "emphasis")], template="slam", intensity="bold",
               style=EditStyle(intensity="bold", sfx=False))
    assert not cues(plan, "sfx") and cues(plan, "accent")


# ============================================================================= §8.5 rules


def test_rule1_serious_mode_forces_subtle_and_removes_comedy():
    swear = mid("raining", "swear")
    beats = [mid(HOOK, "hook", strength=3), swear, mid("wallet", "emphasis", strength=3),
             mid("hugged us both", "punchline", strength=3), mid("a long time.", "payoff", strength=3)]
    meta = {"sensitivity": "serious", "reactions": ({"at": 30.0, "query": "lol"},)}
    for template in TEMPLATES:
        plan = run(beats, template=template, intensity="max", meta=meta, reactions_available=True,
                   style=EditStyle(intensity="max", reactions=True))
        assert not ({"pop", "boom", "record_scratch", "cash_register", "slap", "tape_stop"} & sounds(plan))
        assert not cues(plan, "freeze") and not cues(plan, "speed") and not cues(plan, "reaction")
        assert not [c for c in cues(plan, "accent") if c.variant in ("emoji", "emoji_burst", "doodle")]
        assert cues(plan, "bleep")
        assert plan.music is None                        # auto + serious: no bed
        assert plan.grade == "none"                      # subtle budgets
        assert len(cues(plan, "transition")) <= 1
    plan = run(beats, intensity="max", meta=meta, style=EditStyle(intensity="max", music_mood="hype"))
    assert plan.music is not None and plan.music.mood == "inspiring"


@pytest.mark.parametrize("censor,cover", [("bleep", "none"), ("bleep_bar", "bar"), ("bleep_pixelate", "pixelate")])
def test_rule2_bleep_cover_follows_censor(censor, cover):
    plan = run([mid("raining", "swear")], style=EditStyle(intensity="subtle", censor=censor))
    assert one(plan, "b0.bleep").cover == cover


def test_rule2_bleeps_ignore_budgets_and_spacing():
    w = words_from("damn damn damn damn damn damn damn damn and then it ended for real this time okay.")
    beats = [beat(w, "damn", "swear", after=i) for i in range(8)]
    plan = run(beats, words=w, intensity="subtle")
    assert len(cues(plan, "bleep")) == 8


def test_rule2_no_bleeps_with_approx_timing_or_censor_off():
    b = mid("raining", "swear")
    assert not cues(run([b], meta={"timing": "approx"}), "bleep")
    assert not cues(run([b], style=EditStyle(intensity="bold", censor="off")), "bleep")
    assert not cues(run([b], intensity="off"), "bleep")              # D3: style-only changes no audio


def test_rule2_censor_text_applies_to_accents_even_with_approx_timing():
    w = words_from("This is total bullshit and everybody knows it. " * 4)
    swear = beat(w, "bullshit", "swear")
    emph = beat(w, "total bullshit", "emphasis", strength=2)
    plan = run([swear, emph], words=w, template="marker_circle", intensity="subtle", meta={"timing": "approx"})
    accents = cues(plan, "accent")
    assert accents and all("bullshit" not in c.text for c in accents)


def test_rule3_quiet_zones():
    w = words_from("Wow this is wild and everyone is watching closely right now. " * 3, start=0.05)
    wow = beat(w, "Wow", "emphasis", strength=3)
    last = beat(w, "now.", "emphasis", strength=3, after=len(w) - 1)
    plan = run([wow, last], words=w, intensity="max", template="marker_circle",
               duration=round(w[-1]["end"] + 0.1, 3))
    dur = plan.edit_duration
    assert all(c.start >= 0.25 - 1e-6 or c.kind == "bleep" for c in plan.cues)
    # nothing STARTS in the tail; a reframe or nudge started earlier may hold to the end of the clip
    assert all(c.end <= dur - 0.3 + 1e-6 or (c.kind == "zoom" and c.start <= dur - 0.6 + 1e-6)
               for c in plan.cues if c.kind != "bleep")
    hooked = run([beat(w, "Wow", "hook", strength=3)], words=w, template="highlighter_swipe",
                 intensity="bold")
    assert any(c.start < 0.25 for c in hooked.cues)          # caption keywords may sit in the head zone
    looped = run([wow, last], words=w, intensity="max", template="marker_circle",
                 duration=round(w[-1]["end"] + 0.1, 3), style=EditStyle(intensity="max", loop_ending=True))
    assert any(c.end > looped.edit_duration - 0.3 for c in looped.cues)


def test_rule4_broll_windows_exclude_picture_fx():
    b = mid("wallet", "emphasis", strength=3)
    window = ((b.start - 1.0, b.end + 1.0),)
    for template in ("behind_head", "face_doodles", "marker_circle"):
        plan = run([b], template=template, intensity="max", broll_windows=window)
        for c in plan.cues:
            inside = c.start < window[0][1] and window[0][0] < max(c.end, c.start + 1e-6)
            if inside and (c.kind in ("zoom", "shake", "transition", "glitch") or (
                c.kind == "accent" and c.variant in ("behind", "doodle")
            )):
                raise AssertionError(describe(c))


def test_rule4_reaction_windows_exclude_picture_fx():
    b = mid("wallet", "emphasis", strength=3)
    meta = {"reactions": ({"at": b.start - 0.5, "query": "shocked"},)}
    plan = run([b], intensity="max", meta=meta, reactions_available=True,
               style=EditStyle(intensity="max", reactions=True))
    react = cues(plan, "reaction")[0]
    assert react.end - react.start == pytest.approx(2.0)
    assert not [c for c in cues(plan, "zoom") if c.start < react.end and react.start < c.end]


def test_rule4_no_zoom_within_04s_of_transition_and_one_full_frame_fx_at_a_time():
    b = mid("ground.", "emphasis", strength=3)
    cut = round(b.end + 0.2, 3)
    plan = run([b], intensity="max", cuts=(cut,), passage_breaks=(cut,))
    assert cues(plan, "transition")
    for z in cues(plan, "zoom"):
        assert max(0.0, max(z.start, cut) - min(z.end, cut)) >= 0.4 - 1e-6


def test_rule4_full_frame_hits_08s_apart_across_beats():
    w = words_from("Boom one. Boom two. Boom three. Boom four. and then everything went quiet for a while. " * 3)
    beats = [beat(w, f"Boom {n}.", "punchline", strength=3) for n in ("one", "two", "three", "four")]
    plan = run(beats, words=w, intensity="max", template="slam")
    hits = [(c.start, c.beat) for c in plan.cues
            if c.kind in ("flash", "glitch", "transition", "shake") or (c.kind == "accent" and c.variant == "slam")]
    for t1, b1 in hits:
        for t2, b2 in hits:
            if b1 != b2:
                assert abs(t1 - t2) >= 0.8 - 1e-6


def test_rule5_hook_band():
    hook = mid(HOOK, "hook", strength=3)
    window = (0.12, 4.0)
    assert run([hook], template="kinetic_stack", intensity="off", hook_window=window).hook_title_suppressed
    assert run([hook], template="slam", intensity="off", hook_window=window).hook_title_suppressed
    assert not run([hook], template="marker_circle", intensity="max", hook_window=window).hook_title_suppressed
    late = mid("wallet", "punchline", strength=3)
    assert not run([late], template="slam", intensity="off", hook_window=window).hook_title_suppressed
    assert not run([hook], template="slam", intensity="off").hook_title_suppressed


def test_rule6_transitions_only_at_cuts_never_inside_a_word():
    inside = next(w for w in WORDS if w["text"] == "wallet")
    word_cut = round((inside["start"] + inside["end"]) / 2, 3)
    gap_cut = round((WORDS[20]["end"] + WORDS[21]["start"]) / 2, 3)
    plan = run([], intensity="max", cuts=(word_cut, gap_cut, 30.0), passage_breaks=())
    times = [c.start for c in cues(plan, "transition")]
    assert word_cut not in times
    assert set(times) <= {gap_cut, 30.0}
    assert all(c.id.startswith("t") and c.id.endswith(".tr") for c in cues(plan, "transition"))


def test_rule6_bold_only_on_passage_breaks_and_ranked_by_strongest_beat():
    cuts = (8.0, 16.0, 24.0)
    assert not cues(run([], intensity="subtle", cuts=cuts, passage_breaks=cuts), "transition")
    assert not cues(run([], intensity="bold", cuts=cuts, passage_breaks=()), "transition")
    assert len(cues(run([], intensity="bold", cuts=cuts, passage_breaks=(16.0, 24.0)), "transition")) == 1
    strong = mid("He drove over", "payoff", strength=3)
    near = round(strong.start - 0.2, 3)          # in the sentence gap before the payoff
    plan = run([strong], intensity="bold", cuts=(9.0, near), passage_breaks=(9.0, near))
    # bold allows one transition on this clip; the passage break near the strongest beat wins
    trs = cues(plan, "transition")
    assert len(trs) == 1 and trs[0].start == near and trs[0].style == "flash"


def test_rule7_no_automatic_warp_and_user_warp_kept():
    beats, kw = payoff_scene()
    beats.append(mid(BUILD, "build", strength=3))
    for intensity in ("subtle", "bold", "max"):
        plan = run(beats, intensity=intensity, **kw)
        assert not cues(plan, "speed") and not cues(plan, "freeze")
    user = SpeedCue(id="u.speed", start=10.0, end=12.0, rate=1.3, origin="user")
    assert one(run(beats, intensity="max", user_cues=(user,), **kw), "u.speed") == user


def test_rule8_resolver_never_emits_kenburns():
    for name in ("story", "talking_head", "no_timing"):
        inp, seed = load_fixture(name)
        for template in TEMPLATES:
            for intensity in INTENSITIES:
                plan = resolve(inp, EditStyle(intensity=intensity), template, seed)
                assert not [c for c in plan.cues if c.id.startswith("kb")]


def test_rule9_music_mood_bed_and_levels():
    plan = run([mid("wallet", "emphasis")], intensity="bold", meta={"mood": "funny"})
    # constant beds at subtle and bold, a 7 dB duck at max (levels calibrated against the references)
    assert (plan.music.mood, plan.music.gain_db, plan.music.duck_db) == ("funny", -15, 0)
    assert plan.music.bed in BEDS["funny"] and len(BEDS["funny"]) > 1
    subtle = run([], intensity="subtle", meta={"mood": "funny"}).music
    assert (subtle.gain_db, subtle.duck_db) == (-8, 0)
    wild = run([], intensity="max", meta={"mood": "funny"}).music
    assert (wild.gain_db, wild.duck_db, wild.attack, wild.release) == (3, -7, 0.25, 0.6)
    plan = run([], style=EditStyle(intensity="bold", music_mood="dark"))
    assert plan.music.mood == "dark" and plan.music.bed in BEDS["dark"]
    assert run([], style=EditStyle(intensity="bold", music=False)).music is None
    assert run([], template="slam", intensity="off").music is None


def test_rule9_the_bed_is_a_seeded_pick_among_the_moods_beds():
    picks = {run([], seed=seed, meta={"mood": "chill"}).music.bed for seed in range(40)}
    assert picks <= set(BEDS["chill"]) and len(picks) > 1
    assert run([], seed=7, meta={"mood": "chill"}).music.bed == run([], seed=7, meta={"mood": "chill"}).music.bed


def test_mood_music_trim_raises_the_aggressive_and_hype_beds():
    # A drill or trap bed under a rant is meant to be felt: aggressive and hype sit 4 and 2 dB over the tier level.
    assert MOOD_MUSIC_TRIM_DB == {"aggressive": 4.0, "hype": 2.0}
    for intensity in ("subtle", "bold", "max"):
        gain, duck, _attack, _release = MUSIC_LEVELS[intensity]
        for mood in MOODS:
            music = run([], style=EditStyle(intensity=intensity, music_mood=mood)).music
            assert music.mood == mood and music.bed in BEDS[mood]
            assert (music.gain_db, music.duck_db) == (gain + MOOD_MUSIC_TRIM_DB.get(mood, 0.0), duck), (intensity, mood)


def test_rule9_aggressive_has_its_own_beds():
    # Aggressive has built-in beds of its own (no borrowing); the plan names one of them and keeps the mood, so
    # aggressive drop-ins (FX_LIBRARY_DIR music/aggressive-*) still replace it at mix time.
    picked = run([], style=EditStyle(intensity="bold", music_mood="aggressive"))
    assert (picked.music.mood, picked.mood) == ("aggressive", "aggressive")
    assert picked.music.bed in BEDS["aggressive"] and picked.music.bed.startswith("aggressive-")
    auto = run([], intensity="bold", meta={"mood": "aggressive"})
    assert auto.music.mood == "aggressive" and auto.music.bed in BEDS["aggressive"]
    # Louder than any other mood at the tier (MOOD_MUSIC_TRIM_DB).
    assert (auto.music.gain_db, auto.music.duck_db) == (MUSIC_LEVELS["bold"][0] + 4.0, 0)
    # Serious clips keep the inspiring bed whatever the pick, and a mood without built-in beds gets no music.
    serious = run([], style=EditStyle(intensity="bold", music_mood="aggressive"),
                  meta={"mood": "aggressive", "sensitivity": "serious"})
    assert serious.music.mood == "inspiring" and serious.music.bed in BEDS["inspiring"]
    assert run([], intensity="bold", meta={"mood": "aggressive"}, beds={"chill": BEDS["chill"]}).music is None


def test_aggressive_is_not_a_calm_mood():
    from src.edit_fx.resolver import CALM_MOODS
    assert "aggressive" not in CALM_MOODS


def test_rule9_loop_ending_offsets_the_bed_to_the_seam():
    plan = run([], style=EditStyle(intensity="bold", loop_ending=True))
    length = BED_LENGTHS[plan.music.bed]
    assert plan.music.offset == pytest.approx((length - plan.edit_duration % length) % length, abs=1e-3)
    assert plan.music.fade_in == plan.music.fade_out == 0.02
    assert plan.loop_ending == "crossfade"


def test_rule10_retention_grade_and_grain():
    assert run([], intensity="subtle").grade == "none"
    assert run([], intensity="bold").grade == "none"
    assert run([], intensity="max").grade == "punchy"
    assert run([], intensity="max", meta={"mood": "dramatic"}).grade == "punchy"
    assert run([], style=EditStyle(intensity="subtle", grade="vhs")).grade == "vhs"
    assert [run([], style=EditStyle(intensity=i, grain=True)).grain for i in ("subtle", "bold", "max")] == [
        0.15, 0.2, 0.3]
    plan = run([], style=EditStyle(intensity="bold", progress_bar=True))
    assert plan.progress_bar and plan.loop_ending == "none"


def test_rule11_reactions_opt_in_and_available():
    b = mid("hugged us both", "punchline", strength=3)
    meta = {"reactions": ({"at": b.start, "query": "lol"}, {"at": 5.0, "query": "wow"})}
    on = EditStyle(intensity="bold", reactions=True)
    assert len(cues(run([b], style=on, meta=meta, reactions_available=True), "reaction")) == 1
    assert not cues(run([b], style=on, meta=meta, reactions_available=False), "reaction")
    assert not cues(run([b], intensity="bold", meta=meta, reactions_available=True), "reaction")
    two = cues(run([b], style=EditStyle(intensity="max", reactions=True), meta=meta, reactions_available=True),
               "reaction")
    assert len(two) == 2 and {c.query for c in two} == {"lol", "wow"}
    blocked = run([b], style=on, meta=meta, reactions_available=True, broll_windows=((b.start - 1, b.start + 3),))
    assert [c.query for c in cues(blocked, "reaction")] == ["wow"]


def test_rule12_user_cues_kept_and_collisions_dropped():
    b = mid("wallet", "emphasis", strength=2)
    user_zoom = ZoomCue(id="u1", start=b.start + 0.1, end=b.start + 0.6, mode="punch", scale=1.3, origin="user")
    user_sfx = SfxCue(id="u2", start=b.start, end=b.start, sound="slap", origin="user")
    plan = run([b], intensity="bold", user_cues=(user_zoom, user_sfx))
    assert one(plan, "u1") == user_zoom and one(plan, "u2") == user_sfx
    for c in plan.cues:
        if c.origin == "auto" and c.kind in ("zoom", "sfx"):
            assert max(0.0, max(c.start, user_zoom.start) - min(c.end, user_zoom.end)) >= 0.3 - 1e-6 or c.kind == "sfx"
            assert c.kind != "sfx" or abs(c.start - user_sfx.start) >= 0.3 - 1e-6


def test_rule12_suppressed_ids_never_emitted_and_user_ids_not_duplicated():
    b = mid("wallet", "emphasis", strength=2)
    full = run([b], intensity="bold")
    zoom_id = next(c.id for c in cues(full, "zoom") if c.mode == "punch")
    sfx_id = next(c.id for c in cues(full, "sfx"))
    plan = run([b], intensity="bold", suppressed=frozenset({zoom_id}))
    assert not has(plan, zoom_id) and has(plan, sfx_id)
    moved = ZoomCue(id=zoom_id, start=20.0, end=20.5, mode="punch", scale=1.2, origin="user")
    plan = run([b], intensity="bold", user_cues=(moved,))
    assert [c for c in plan.cues if c.id == zoom_id] == [moved]


# ============================================================================= §8.6 modes


def test_style_only_accents_and_intrinsics_no_music_zoom_or_warp():
    beats = [mid(HOOK, "hook", strength=3), mid("wallet", "emphasis", strength=3),
             mid("a long time.", "payoff", strength=3), mid(BUILD, "build", strength=3)]
    for template in FX_TEMPLATES:
        plan = run(beats, template=template, intensity="off", cuts=(15.0,), passage_breaks=(15.0,))
        kinds = {c.kind for c in plan.cues}
        assert kinds <= {"accent", "sfx", "flash", "desaturate"}, (template, kinds)
        assert plan.music is None and plan.grade == "none" and not plan.progress_bar
        sfx_ids = [c.id for c in cues(plan, "sfx")]
        assert all(i.split(".")[1] in ("accent", "slam", "behind", "burst") for i in sfx_ids), sfx_ids
    assert cues(run(beats, template="slam", intensity="off"), "flash")
    assert cues(run(beats, template="kinetic_stack", intensity="off"), "desaturate")


def test_legacy_template_with_intensity_has_no_accents():
    beats = [mid(HOOK, "hook", strength=3), mid("wallet", "emphasis", strength=3)]
    for legacy in ("default", "hormozi", "mrbeast", "minimal", "tiktok", "neon", "podcast"):
        plan = run(beats, template=legacy, intensity="max")
        assert not cues(plan, "accent") and cues(plan, "zoom")


def test_no_timing_only_global_fx():
    inp, seed = load_fixture("no_timing")
    plan = resolve(inp, EditStyle(intensity="bold", grain=True, progress_bar=True), "marker_circle", seed)
    assert {c.kind for c in plan.cues} <= {"transition", "sfx"}
    assert all(c.kind == "transition" or c.id.endswith(".tr.sfx") or c.id.startswith("e") for c in plan.cues)
    # the fixture's mood is hype: the bold level plus its trim, nothing to duck under
    assert plan.music.gain_db == -15 + MOOD_MUSIC_TRIM_DB["hype"] and plan.music.duck_db == 0
    assert plan.grade == "none" and plan.grain == 0.2 and plan.progress_bar and plan.timing == "none"


# ============================================================================= §8.7 template variants


def test_variant_marker_circle():
    w = words_from("I will never ever do that again because it was a total mess. " * 4)
    plan = run([beat(w, "never", "emphasis"), beat(w, "total mess.", "punchline", after=10),
                beat(w, "again", "emphasis", after=25), beat(w, "it was", "number", after=40, value=1.0)],
               words=w, template="marker_circle", intensity="subtle")
    assert [c.variant for c in cues(plan, "accent")] == ["strike", "underline", "circle", "arrow"]


def test_variant_emoji_drop_only_literal_emoji_never_bursts():
    plan = run([mid("wallet", "emphasis", strength=3, tone="funny")], template="emoji_drop", intensity="max")
    assert not [c for c in cues(plan, "accent") if c.variant in ("emoji", "emoji_burst")]   # no tone faces
    plan = run([mid("wallet", "emphasis", strength=2, emoji="💰")], template="emoji_drop", intensity="max")
    accent = one(plan, "b0.accent")
    assert (accent.variant, accent.emoji) == ("emoji", "💰")
    assert not [c for c in cues(plan, "accent") if c.variant == "emoji_burst"]


def test_variant_slam_top_strength3_and_intrinsic_visuals():
    beats = [mid(HOOK, "hook", strength=3), mid("wallet", "emphasis", strength=3),
             mid("a long time.", "payoff", strength=3)]
    plan = run(beats, template="slam", intensity="bold")
    variants = {c.beat: c.variant for c in cues(plan, "accent")}
    assert variants["b1"] == "highlight"
    slams = [b for b, v in variants.items() if v == "slam"]
    assert 1 <= len(slams) <= 3
    for bid in slams:
        slam = one(plan, f"{bid}.slam")
        assert len(slam.text.split()) == 1                       # the keyword alone
        assert has(plan, f"{bid}.slam.flash")
    plan_off = run(beats, template="slam", intensity="off")
    assert not cues(plan_off, "shake") and not cues(plan_off, "glitch")


def test_variant_behind_head_limits():
    w = words_from("This changed my life forever and I mean it. " * 8)
    beats = [beat(w, "life", "emphasis", strength=3, after=i * 9) for i in range(6)]
    plan = run(beats, words=w, template="behind_head", intensity="max", duration=round(w[-1]["end"] + 0.5, 3))
    behind = [c for c in cues(plan, "accent") if c.variant == "behind"]
    assert 1 <= len(behind) <= max(1, math.floor(plan.edit_duration / 12 + 0.5))
    assert all(c.end - c.start <= 2.0 + 1e-6 and len(c.text.split()) == 1 for c in behind)


def test_variant_stat_counter():
    plan = run([mid("a phone number", "number", value=12.0, prefix="$", suffix="k"),
                mid("last year.", "number", value=None), mid("wallet", "emphasis")],
               template="stat_counter", intensity="subtle")
    by_beat = {c.beat: c for c in cues(plan, "accent")}
    variants = sorted(c.variant for c in by_beat.values())
    assert variants == ["arrow", "counter", "highlight"]
    counter = next(c for c in by_beat.values() if c.variant == "counter")
    assert (counter.value, counter.prefix, counter.suffix) == (12.0, "$", "k")


def test_variant_kinetic_stack_hook_sentence_and_desaturate():
    plan = run([mid("Okay so here is the thing", "hook", strength=2), mid("wallet", "emphasis")],
               template="kinetic_stack", intensity="off")
    kinetic = one(plan, "b0.accent")
    assert kinetic.variant == "kinetic" and kinetic.text.endswith("story")
    assert len(kinetic.text.split()) <= 12
    desat = one(plan, "b0.desat")
    assert (desat.amount, desat.dim) == (0.7, 0.2)
    assert one(plan, "b1.accent").variant == "highlight"
    no_hook = run([mid("wallet", "emphasis")], template="kinetic_stack", intensity="off")
    assert one(no_hook, "g.kinetic").variant == "kinetic"


def test_variant_decode_glitch_keeps_its_sound_but_no_glitch_hit():
    for intensity in ("subtle", "bold", "max"):
        plan = run([mid("wallet", "emphasis")], template="decode_glitch", intensity=intensity)
        assert one(plan, "b0.accent").variant == "decode"
        assert not cues(plan, "glitch")


def test_variant_highlighter_swipe_marks_keywords_not_beats():
    plan = run([mid("wallet", "emphasis"), mid("a phone number", "number", value=1.0),
                mid("hugged us both", "punchline")], template="highlighter_swipe", intensity="bold")
    kws = keywords(plan)
    assert kws and all(c.id.endswith(".kw") for c in kws)
    assert {"wallet", "number", "hugged"} & {c.text for c in kws}
    assert not [c for c in cues(plan, "accent") if not c.id.endswith(".kw")]


@pytest.mark.parametrize("tone,kind,strength,expected", [
    ("hype", "emphasis", 2, ["sparkles"]), ("wholesome", "emphasis", 2, ["sparkles"]),
    ("wholesome", "emphasis", 3, ["heart"]), ("funny", "punchline", 2, ["sparkles", "bang"]),
    ("shocked", "emphasis", 2, ["bang"]), ("awkward", "emphasis", 2, ["sweat"]), ("sad", "emphasis", 2, ["sweat"]),
    ("smug", "emphasis", 2, ["horns"]), ("neutral", "emphasis", 2, ["arrows"]),
])
def test_variant_face_doodles_by_tone(tone, kind, strength, expected):
    plan = run([mid("wallet", kind, strength=strength, tone=tone)], template="face_doodles", intensity="off")
    assert [c.doodle for c in cues(plan, "accent")] == expected


def test_variant_face_doodles_angry_and_swear():
    for kind, tone in (("emphasis", "angry"), ("swear", "neutral")):
        plan = run([mid("wallet", kind, tone=tone)], template="face_doodles", intensity="off")
        assert [c.doodle for c in cues(plan, "accent")][0] in ("horns", "anger")


# ============================================================================= determinism


def test_determinism():
    inp, seed = load_fixture("talking_head")
    for template in TEMPLATES:
        a = resolve(inp, EditStyle(intensity="max"), template, seed)
        b = resolve(inp, EditStyle(intensity="max"), template, seed)
        assert a == b
    assert resolve(inp, EditStyle(intensity="bold"), "slam", seed) != resolve(
        inp, EditStyle(intensity="bold"), "slam", seed + 1)


@pytest.mark.parametrize("name", ["story", "talking_head"])
def test_removing_one_beat_leaves_other_cues_unchanged(name):
    inp, seed = load_fixture(name)
    for template in ("marker_circle", "slam", "stat_counter", "face_doodles", "default"):
        for intensity in ("subtle", "bold", "max"):
            full = {c.id: c for c in resolve(inp, EditStyle(intensity=intensity), template, seed).cues}
            for drop in inp.beats.beats:
                beats = inp.beats.model_copy(update={"beats": tuple(b for b in inp.beats.beats if b.id != drop.id)})
                smaller = ResolveInput(**{**inp.__dict__, "beats": beats})
                for cue in resolve(smaller, EditStyle(intensity=intensity), template, seed).cues:
                    assert cue.beat != drop.id
                    before = full.get(cue.id)
                    if before is None or before.beat == drop.id:
                        continue           # a cue that is new, or that the removed beat used to own (g.wait)
                    if re.fullmatch(r"[zne]\d+\.(zoom|sfx)|w\d+\.kw", cue.id):
                        continue           # fx-v2 global steps (reframes, nudges, cut hits, keywords) are placed
                                           # over all beats at once; their ids are frame / word numbers
                    if drop.kind in ("payoff", "punchline") and cue.kind == "sfx" and cue.sound == "riser":
                        continue           # §8.4: a build's riser ends at the NEXT payoff/punchline
                    if drop.kind == "swear" and cue.kind == "accent":
                        # without the swear its word is no longer censored: only the displayed text may change
                        assert cue.model_copy(update={"text": before.text}) == before
                        continue
                    assert cue == before, (template, intensity, drop.id, cue.id)


# ============================================================================= goldens


@pytest.mark.parametrize("name", ["talking_head", "story", "no_timing"])
def test_goldens(name):
    data = json.loads((FIXTURES / f"{name}.input.json").read_text())
    expected = json.loads((FIXTURES / f"{name}.golden.json").read_text())
    actual = goldens_for(data)
    assert actual.keys() == expected.keys()
    for key in expected:
        assert actual[key]["cues"] == expected[key]["cues"], (
            f"{name} {key} changed; rerun scripts/fx/gen_resolver_goldens.py --accept if intended")
        assert actual[key] == expected[key], key


def test_golden_sounds_are_the_reference_palette():
    """fx-v2: sounds follow visual events: hits and booms on reframes, pops on graphics, a whoosh on a flash, and the
    hidden templates' own sounds (slam, counter, sticker, decode). No risers, scratches or tape stops."""
    heard: set[str] = set()
    for name in ("talking_head", "story", "no_timing"):
        for entry in json.loads((FIXTURES / f"{name}.golden.json").read_text()).values():
            for line in entry["cues"]:
                m = re.search(r" sfx +(\w+)#", line)
                if m:
                    heard.add(m.group(1))
    assert {"impact", "boom", "pop", "whoosh"} <= heard <= set(SOUND_IDS) - {"riser", "record_scratch", "tape_stop"}


def test_story_after_default_cleanup_never_warps_and_resets_reframes_at_cuts():
    inp, seed = load_fixture("story")
    assert inp.cuts and inp.passage_breaks           # real cleanup cut long pauses and the 3.6 s gap
    for template in TEMPLATES:
        for intensity in ("bold", "max"):
            plan = resolve(inp, EditStyle(intensity=intensity), template, seed)
            assert not cues(plan, "speed") and not cues(plan, "freeze"), template
            for z in cues(plan, "zoom"):
                if z.mode == "punch":
                    assert not any(z.start + 1e-6 < c < z.end - 1e-6 for c in inp.cuts)


# ============================================================================= review fixes


def test_rule12_suppressing_a_visual_drops_its_intrinsic_children():
    b = mid("a long time.", "payoff", strength=3)
    full = run([b], template="slam", intensity="bold")
    children = [c.id for c in full.cues if c.id.startswith("b0.slam.")]
    assert has(full, "b0.slam") and children
    plan = run([b], template="slam", intensity="bold", suppressed=frozenset({"b0.slam"}))
    assert not [c for c in plan.cues if c.id == "b0.slam" or c.id.startswith("b0.slam.")]
    # unrelated cues of the same beat stay exactly as they were
    others = {c.id: c for c in full.cues if not c.id.startswith("b0.slam")}
    assert {c.id: c for c in plan.cues} == others


def test_slam_template_hook_always_slams_any_strength():
    plan = run([mid(HOOK, "hook", strength=1), mid("a long time.", "payoff", strength=3)],
               template="slam", intensity="subtle")
    assert one(plan, "b0.slam").variant == "slam"


def test_swear_gets_a_bleep_but_no_glitch():
    b = mid("raining", "swear")
    plan = run([b], intensity="max")
    assert has(plan, "b0.bleep") and not cues(plan, "glitch")


def test_long_beat_ids_never_raise():
    b = mid("a phone number", "number", value=12.0, prefix="$").model_copy(update={"id": "u" + "9" * 23})
    plan = run([b], template="stat_counter", intensity="max")
    assert all(len(c.id) <= 32 for c in plan.cues)


def test_loop_music_offset_uses_the_emitted_warp():
    from src.edit_fx.warp import build_warp

    beats, kw = payoff_scene()
    user = SpeedCue(id="u.speed", start=10.0, end=12.0, rate=1.5, origin="user")
    plan = run(beats, style=EditStyle(intensity="max", loop_ending=True), user_cues=(user,), **kw)
    out = build_warp(plan.cues, plan.edit_duration).out_duration
    assert out < plan.edit_duration
    length = BED_LENGTHS[plan.music.bed]
    assert plan.music.offset == pytest.approx((length - out % length) % length, abs=1e-3)
