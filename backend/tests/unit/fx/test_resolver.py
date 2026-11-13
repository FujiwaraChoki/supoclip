"""Resolver (§8) named tests: every §8.4 cell, the intrinsic-SFX table, every §8.5 rule, the §8.7 variants, goldens."""

from __future__ import annotations

import json
import re

import pytest

from src.edit_fx.contracts import (
    SOUND_IDS,
    BleepCue,
    EditStyle,
    FlashCue,
    SfxCue,
    SpeedCue,
    ZoomCue,
)
from src.edit_fx.resolver import RISER_SECONDS, ResolveInput, resolve, sfx_bus_gain_db
from tests.fixtures.fx_resolver.builders import (
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


def run(beats, *, template="default", intensity="spicy", words=WORDS, seed=SEED, style=None, meta=None, **kw):
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


# ============================================================================= §8.4 bundles: hook


def test_bundle_hook_chill_whoosh_and_push():
    plan = run([mid(HOOK, "hook", strength=1)], intensity="chill")
    hook = plan.cues[0]
    assert one(plan, "b0.sfx").sound == "whoosh" and one(plan, "b0.sfx").start == hook.start
    push = one(plan, "b0.push")
    assert push.mode == "push" and push.scale == 1.04
    assert not has(plan, "b0.zoom")


@pytest.mark.parametrize("template,cue_id,variant", [("kinetic_stack", "b0.accent", "kinetic"),
                                                     ("slam", "b0.slam", "slam"),
                                                     ("behind_head", "b0.behind", "behind")])
def test_bundle_hook_chill_template_treatment(template, cue_id, variant):
    plan = run([mid(HOOK, "hook", strength=2)], intensity="chill", template=template)
    assert one(plan, cue_id).variant == variant


def test_bundle_hook_spicy_punch_at_strongest_word_and_bigger_push():
    plan = run([mid(HOOK, "hook", strength=1)], intensity="spicy")
    assert one(plan, "b0.push").scale == 1.06
    punch = one(plan, "b0.zoom")
    assert punch.mode == "punch" and punch.scale == 1.12
    story = next(w for w in WORDS if w["text"] == "story.")
    assert punch.start == story["start"]   # the last content word carries phrase stress


def test_bundle_hook_unhinged_flash_shake_boom():
    plan = run([mid(HOOK, "hook", strength=1)], intensity="unhinged")
    assert one(plan, "b0.push").scale == 1.08
    assert isinstance(one(plan, "b0.flash"), FlashCue)
    assert one(plan, "b0.shake").amp == 18
    assert one(plan, "b0.sfx2").sound == "boom"
    assert one(plan, "b0.shake").start == one(plan, "b0.sfx2").start


# ============================================================================= emphasis


def test_bundle_emphasis_chill_accent_only():
    plan = run([mid("wallet", "emphasis", strength=3)], intensity="chill", template="marker_circle")
    assert {c.kind for c in plan.cues} == {"accent"}


def test_bundle_emphasis_spicy_strength2_gets_pop_and_hard_115_cut():
    plan = run([mid("wallet", "emphasis", strength=2)], intensity="spicy")
    assert one(plan, "b0.sfx").sound == "pop"
    punch = one(plan, "b0.zoom")
    assert (punch.mode, punch.scale) == ("punch", 1.15)


def test_bundle_emphasis_spicy_strength1_gets_nothing_extra():
    plan = run([mid("wallet", "emphasis", strength=1)], intensity="spicy")
    assert not cues(plan, "zoom") and not cues(plan, "sfx")


def test_bundle_emphasis_unhinged_punch_range_at_strength1():
    for seed in range(20):
        plan = run([mid("wallet", "emphasis", strength=1)], intensity="unhinged", seed=seed)
        assert 1.15 <= one(plan, "b0.zoom").scale <= 1.25
        assert one(plan, "b0.sfx").sound == "pop"


# ============================================================================= number


def test_bundle_number_chill_accent_and_stamp():
    plan = run([mid("a phone number", "number", strength=1, value=None)], intensity="chill",
               template="marker_circle")
    assert one(plan, "b0.accent").variant == "arrow"
    assert one(plan, "b0.sfx").sound == "stamp"


def test_bundle_number_chill_counter_ticker_and_stamp_at_lock():
    b = mid("last year.", "number", strength=1, value=1.0)
    plan = run([b], intensity="chill", template="stat_counter")
    counter = one(plan, "b0.accent")
    assert counter.variant == "counter" and counter.value == 1.0
    assert one(plan, "b0.accent.sfx").sound == "ticker" and one(plan, "b0.accent.sfx").start == counter.start
    lock = counter.start + max(0.6, (b.end - b.start) + 0.4)
    stamp = one(plan, "b0.accent.sfx2")
    assert stamp.sound == "stamp" and stamp.start == pytest.approx(lock, abs=1e-3)
    assert not has(plan, "b0.sfx")      # the counter's lock IS the number's stamp


def test_bundle_number_spicy_cash_register_for_currency():
    plan = run([mid("a phone number", "number", strength=2, prefix="$", value=5.0)], intensity="spicy")
    assert one(plan, "b0.sfx").sound == "cash_register"
    plan = run([mid("a phone number", "number", strength=2, value=5.0)], intensity="spicy")
    assert one(plan, "b0.sfx").sound == "stamp"


def test_bundle_number_unhinged_punch_118_and_shake_10():
    plan = run([mid("a phone number", "number", strength=1, value=5.0)], intensity="unhinged")
    assert one(plan, "b0.zoom").scale == 1.18
    assert one(plan, "b0.shake").amp == 10


# ============================================================================= punchline


def test_bundle_punchline_chill_accent():
    plan = run([mid("hugged us both", "punchline", strength=3)], intensity="chill", template="marker_circle")
    assert one(plan, "b0.accent").variant == "underline"
    assert not cues(plan, "zoom") and not cues(plan, "sfx")


@pytest.mark.parametrize("strength,sound", [(3, "boom"), (2, "pop")])
def test_bundle_punchline_spicy_punch_and_sound(strength, sound):
    plan = run([mid("hugged us both", "punchline", strength=strength)], intensity="spicy")
    assert one(plan, "b0.zoom").scale == 1.15
    assert one(plan, "b0.sfx").sound == sound


def test_bundle_punchline_unhinged_glitch_reaction_and_slow_tail():
    b = mid("a long time.", "punchline", strength=1)
    plan = run([b], intensity="unhinged")
    glitch = one(plan, "b0.glitch")
    assert glitch.end - glitch.start == pytest.approx(0.15, abs=1e-3)
    slow = one(plan, "b0.slow")
    assert slow.rate == 0.5
    nxt = next(w for w in WORDS if w["start"] > b.end)
    assert b.end <= slow.start < slow.end <= nxt["start"]
    assert one(plan, "b0.slow.sfx").sound == "tape_stop"
    # the reaction lands on the punchline; its window then keeps the glitch and the punch out (rule 4)
    eb_meta = {"reactions": ({"at": b.start, "query": "crying laughing"},)}
    plan = run([b], intensity="unhinged", meta=eb_meta, reactions_available=True,
               style=EditStyle(intensity="unhinged", reactions=True))
    assert cues(plan, "reaction") and not has(plan, "b0.glitch") and not has(plan, "b0.zoom")


# ============================================================================= swear


@pytest.mark.parametrize("intensity", ["chill", "spicy", "unhinged"])
def test_bundle_swear_always_bleeps(intensity):
    b = mid("raining", "swear")
    plan = run([b], intensity=intensity)
    bleep = one(plan, "b0.bleep")
    assert isinstance(bleep, BleepCue) and bleep.tone is True
    assert bleep.start == pytest.approx(b.start - 0.02) and bleep.end == pytest.approx(b.end + 0.02)
    assert has(plan, "b0.glitch") == (intensity == "unhinged")
    if intensity == "unhinged":
        assert one(plan, "b0.glitch").end - one(plan, "b0.glitch").start == pytest.approx(0.1, abs=1e-3)


# ============================================================================= emotional_turn


def test_bundle_emotional_turn_chill_push_strength3_only():
    phrase = "who passed last year."
    plan = run([mid(phrase, "emotional_turn", strength=3)], intensity="chill")
    push = one(plan, "b0.push")
    assert push.scale == 1.04 and push.end - push.start >= 1.0 - 1e-6
    plan = run([mid(phrase, "emotional_turn", strength=2)], intensity="chill")
    assert not plan.cues


def test_bundle_emotional_turn_spicy_push_and_riser_ending_at_turn():
    b = mid("who passed last year.", "emotional_turn", strength=2)
    plan = run([b], intensity="spicy")
    assert one(plan, "b0.push").scale == 1.06
    riser = one(plan, "b0.sfx2")
    assert riser.sound == "riser"
    assert riser.start + RISER_SECONDS[riser.variant] == pytest.approx(b.start, abs=1e-3)


def test_bundle_emotional_turn_unhinged_desaturate():
    b = mid("who passed last year.", "emotional_turn", strength=1)
    plan = run([b], intensity="unhinged")
    desat = one(plan, "b0.desat")
    assert desat.amount == 0.6 and desat.start == b.start and desat.end - desat.start == pytest.approx(1.0)


# ============================================================================= build


BUILD = "We called the number and a man answered right away."
PAYOFF = "He said the wallet belonged to his father"


def test_bundle_build_chill_nothing():
    plan = run([mid(BUILD, "build", strength=3)], intensity="chill")
    assert not plan.cues


def test_bundle_build_spicy_push_riser_and_speech_ramp():
    build, payoff = mid(BUILD, "build", strength=2), mid(PAYOFF, "payoff", strength=1)
    plan = run([build, payoff], intensity="spicy")
    bid = next(c.beat for c in plan.cues if c.id.endswith(".ramp"))
    assert one(plan, f"{bid}.push").scale == 1.06
    riser = one(plan, f"{bid}.sfx2")
    assert riser.sound == "riser"
    assert riser.start + RISER_SECONDS[riser.variant] == pytest.approx(payoff.start, abs=1e-3)
    ramp = one(plan, f"{bid}.ramp")
    assert isinstance(ramp, SpeedCue) and 1.15 <= ramp.rate <= 1.25
    assert ramp.start <= build.start + 1 / 30 and ramp.end >= build.end - 1 / 30


def test_bundle_build_unhinged_bigger_ramp_and_wordless_speedup():
    words = words_from("Then we waited and waited |0.9| and then the door finally opened. "
                       "Okay everybody calm down now. " * 4)
    build = beat(words, "Then we waited and waited and then the door finally opened.", "build", strength=1)
    plan = run([build], words=words, intensity="unhinged")
    ramp = one(plan, "b0.ramp")
    assert 1.2 <= ramp.rate <= 1.35
    fast = one(plan, "b0.fast")
    gap_start = next(w for w in words if w["text"] == "waited" and w["start"] > words[2]["start"])["end"]
    assert 1.5 <= fast.rate <= 2.5 and fast.start >= gap_start + 0.1 - 1 / 30
    assert ramp.start >= fast.end or ramp.end <= fast.start      # the ramp keeps to the speech around the gap


# ============================================================================= payoff


def payoff_scene(strength=3, duration=None):
    hook = mid(HOOK, "hook", strength=2)
    pay = mid("He drove over and we gave it back to him.", "payoff", strength=strength, label="NO WAY")
    return [hook, pay], ({"duration": duration} if duration else {})


def test_bundle_payoff_chill_accent_only():
    beats, kw = payoff_scene()
    plan = run(beats, intensity="chill", template="marker_circle", **kw)
    assert {c.kind for c in cues(plan, beat_id="b1")} == {"accent"}


def test_bundle_payoff_spicy_wait_punch_impact_freeze_scratch_label():
    beats, kw = payoff_scene()
    hook_end = beats[0].end
    plan = run(beats, intensity="spicy", meta={"hook_end": hook_end}, **kw)
    wait = one(plan, "g.wait")
    assert wait.sticker == "wait_for_it" and wait.end <= beats[1].start
    assert one(plan, "b1.zoom").scale == 1.15
    assert one(plan, "b1.sfx").sound == "impact"
    freeze = one(plan, "b1.freeze")
    assert 0.6 - 1e-6 <= freeze.hold <= 0.8 + 1e-6 and freeze.label == "NO WAY"
    assert freeze.start >= beats[1].end
    scratch = one(plan, "b1.freeze.sfx")
    assert scratch.sound == "record_scratch" and scratch.start == freeze.start


def test_bundle_payoff_spicy_freeze_needs_strength3_and_15s():
    beats, kw = payoff_scene(strength=2)
    assert not cues(run(beats, intensity="spicy", **kw), "freeze")
    beats, _ = payoff_scene(strength=3)
    short = [b for b in beats]
    plan = run(short, intensity="spicy", duration=14.5)
    assert not cues(plan, "freeze")


def test_bundle_payoff_unhinged_freeze_any_strength():
    beats, kw = payoff_scene(strength=1)
    for seed in range(15):
        plan = run(beats, intensity="unhinged", seed=seed, **kw)
        freeze = one(plan, "b1.freeze")
        assert 0.6 - 1e-6 <= freeze.hold <= 1.0 + 1e-6


def test_payoff_label_falls_back_to_first_words():
    hook = mid(HOOK, "hook", strength=2)
    pay = mid("He drove over and we gave it back to him.", "payoff", strength=3)
    plan = run([hook, pay], intensity="unhinged")
    assert one(plan, "b1.freeze").label


# ============================================================================= intrinsic SFX table (AP-H6)


def test_intrinsic_slam_impact_boom_at_unhinged():
    b = mid("wallet", "punchline", strength=3)
    assert one(run([b], template="slam", intensity="off"), "b0.slam.sfx").sound == "impact"
    assert one(run([b], template="slam", intensity="unhinged"), "b0.slam.sfx").sound == "boom"


def test_intrinsic_sticker_and_wait_for_it_slap():
    plan = run([mid("wallet", "emphasis")], template="sticker_slap", intensity="off")
    assert one(plan, "b0.accent.sfx").sound == "slap"
    beats, kw = payoff_scene()
    plan = run(beats, intensity="spicy", meta={"hook_end": beats[0].end})
    assert one(plan, "g.wait.sfx").sound == "slap" and one(plan, "g.wait.sfx").start == one(plan, "g.wait").start


def test_intrinsic_emoji_pop_skipped_within_06s_of_previous_pop():
    w = words_from("We saw a huge massive bear in the woods today. " * 5)
    a = beat(w, "huge", "emphasis", strength=1)
    b = beat(w, "massive", "emphasis", strength=1)
    plan = run([a, b], words=w, template="emoji_drop", intensity="off", style=EditStyle(intensity="off"))
    pops = [c for c in cues(plan, "sfx") if c.sound == "pop"]
    assert len(pops) <= 1


def test_intrinsic_emoji_burst_pop_variant1():
    plan = run([mid("wallet", "emphasis", strength=3)], template="emoji_drop", intensity="spicy")
    burst = one(plan, "b0.burst")
    assert burst.variant == "emoji_burst" and burst.value == 5.0
    assert one(plan, "b0.burst.sfx").sound == "pop" and one(plan, "b0.burst.sfx").variant == 1


def test_intrinsic_decode_glitch_behind_and_kinetic_whoosh():
    assert one(run([mid("wallet", "emphasis")], template="decode_glitch", intensity="off"),
               "b0.accent.sfx").sound == "glitch"
    assert one(run([mid(HOOK, "hook", strength=3)], template="behind_head", intensity="off"),
               "b0.behind.sfx").sound == "whoosh"
    assert one(run([mid(HOOK, "hook", strength=3)], template="kinetic_stack", intensity="off"),
               "b0.accent.sfx").sound == "whoosh"


def test_intrinsic_doodle_pop_only_at_strength2():
    assert has(run([mid("wallet", "emphasis", strength=2)], template="face_doodles", intensity="off"),
               "b0.accent.sfx")
    assert not has(run([mid("wallet", "emphasis", strength=1)], template="face_doodles", intensity="off"),
                   "b0.accent.sfx")


def _transition_plan(style_wanted):
    gap_word = next(i for i, w in enumerate(WORDS) if w["text"] == "ground.")
    cut = round((WORDS[gap_word]["end"] + WORDS[gap_word + 1]["start"]) / 2, 3)
    for seed in range(400):
        plan = run([], intensity="unhinged", seed=seed, cuts=(cut,), passage_breaks=(cut,))
        tr = cues(plan, "transition")
        if tr and tr[0].style == style_wanted:
            return plan, tr[0]
    raise AssertionError(f"no seed gives {style_wanted}")


@pytest.mark.parametrize("style", ["whip", "zoom_through", "spin"])
def test_intrinsic_transition_whoosh_012_before_cut(style):
    plan, tr = _transition_plan(style)
    whoosh = one(plan, f"{tr.id}.sfx")
    assert whoosh.sound == "whoosh" and whoosh.start == pytest.approx(tr.start - 0.12, abs=1e-3)


def test_intrinsic_transition_glitch_sound():
    plan, tr = _transition_plan("glitch")
    assert one(plan, f"{tr.id}.sfx").sound == "glitch"


def test_intrinsic_slow_mo_tape_stop_and_freeze_scratch():
    plan = run([mid("a long time.", "punchline", strength=2)], intensity="unhinged")
    assert one(plan, "b0.slow.sfx").sound == "tape_stop"
    beats, kw = payoff_scene()
    assert one(run(beats, intensity="unhinged", **kw), "b1.freeze.sfx").sound == "record_scratch"


def test_intrinsic_sfx_respect_sfx_toggle():
    plan = run([mid("wallet", "emphasis")], template="sticker_slap", intensity="spicy",
               style=EditStyle(intensity="spicy", sfx=False))
    assert not cues(plan, "sfx") and cues(plan, "accent")


# ============================================================================= §8.5 rules


def test_rule1_serious_mode_forces_chill_and_removes_comedy():
    swear = mid("raining", "swear")
    beats = [mid(HOOK, "hook", strength=3), swear, mid("wallet", "emphasis", strength=3),
             mid("hugged us both", "punchline", strength=3), mid("a long time.", "payoff", strength=3)]
    meta = {"sensitivity": "serious", "reactions": ({"at": 30.0, "query": "lol"},)}
    for template in TEMPLATES:
        plan = run(beats, template=template, intensity="unhinged", meta=meta, reactions_available=True,
                   style=EditStyle(intensity="unhinged", reactions=True))
        assert not ({"pop", "boom", "record_scratch", "cash_register", "slap", "tape_stop"} & sounds(plan))
        assert not cues(plan, "freeze") and not cues(plan, "speed") and not cues(plan, "reaction")
        assert not [c for c in cues(plan, "accent") if c.variant in ("emoji", "emoji_burst", "doodle")]
        assert cues(plan, "bleep")
        assert plan.music is None                        # auto + serious: no bed
        assert plan.grade == "none"                      # chill budgets
        assert len(cues(plan, "transition")) <= 1
    plan = run(beats, intensity="unhinged", meta=meta, style=EditStyle(intensity="unhinged", music_mood="hype"))
    assert plan.music is not None and plan.music.mood == "inspiring"


@pytest.mark.parametrize("censor,cover", [("bleep", "none"), ("bleep_bar", "bar"), ("bleep_pixelate", "pixelate")])
def test_rule2_bleep_cover_follows_censor(censor, cover):
    plan = run([mid("raining", "swear")], style=EditStyle(intensity="chill", censor=censor))
    assert one(plan, "b0.bleep").cover == cover


def test_rule2_bleeps_ignore_budgets_and_spacing():
    w = words_from("damn damn damn damn damn damn damn damn and then it ended for real this time okay.")
    beats = [beat(w, "damn", "swear", after=i) for i in range(8)]
    plan = run(beats, words=w, intensity="chill")
    assert len(cues(plan, "bleep")) == 8


def test_rule2_no_bleeps_with_approx_timing_or_censor_off():
    b = mid("raining", "swear")
    assert not cues(run([b], meta={"timing": "approx"}), "bleep")
    assert not cues(run([b], style=EditStyle(intensity="spicy", censor="off")), "bleep")


def test_rule2_censor_text_applies_to_accents_even_with_approx_timing():
    w = words_from("This is total bullshit and everybody knows it. " * 4)
    swear = beat(w, "bullshit", "swear")
    emph = beat(w, "total bullshit", "emphasis", strength=2)
    plan = run([swear, emph], words=w, template="marker_circle", intensity="chill", meta={"timing": "approx"})
    accents = cues(plan, "accent")
    assert accents and all("bullshit" not in c.text for c in accents)


def test_rule3_quiet_zones():
    w = words_from("Wow this is crazy and everyone is watching closely right now. " * 3, start=0.05)
    wow = beat(w, "Wow", "emphasis", strength=3)
    last = beat(w, "now.", "emphasis", strength=3, after=len(w) - 1)
    plan = run([wow, last], words=w, intensity="unhinged", template="marker_circle",
               duration=round(w[-1]["end"] + 0.1, 3))
    dur = plan.edit_duration
    assert all(c.start >= 0.25 - 1e-6 or c.kind == "bleep" for c in plan.cues)
    assert all(c.end <= dur - 0.3 + 1e-6 for c in plan.cues if c.kind != "bleep")
    hooked = run([beat(w, "Wow this is crazy", "hook", strength=3)], words=w, intensity="spicy")
    assert any(c.start < 0.25 for c in hooked.cues)          # hook-bound cues may sit in the head zone
    looped = run([wow, last], words=w, intensity="unhinged", template="marker_circle",
                 duration=round(w[-1]["end"] + 0.1, 3), style=EditStyle(intensity="unhinged", loop_ending=True))
    assert any(c.end > looped.edit_duration - 0.3 for c in looped.cues)


def test_rule4_broll_windows_exclude_picture_fx():
    b = mid("wallet", "emphasis", strength=3)
    window = ((b.start - 1.0, b.end + 1.0),)
    for template in ("behind_head", "face_doodles", "marker_circle"):
        plan = run([b], template=template, intensity="unhinged", broll_windows=window)
        for c in plan.cues:
            if c.kind in ("zoom", "shake", "transition", "glitch") or (
                c.kind == "accent" and c.variant in ("behind", "doodle")
            ):
                raise AssertionError(describe(c))


def test_rule4_reaction_windows_exclude_picture_fx():
    b = mid("wallet", "emphasis", strength=3)
    meta = {"reactions": ({"at": b.start - 0.5, "query": "shocked"},)}
    plan = run([b], intensity="unhinged", meta=meta, reactions_available=True,
               style=EditStyle(intensity="unhinged", reactions=True))
    react = cues(plan, "reaction")[0]
    assert react.end - react.start == pytest.approx(2.0)
    assert not [c for c in cues(plan, "zoom") if c.start < react.end and react.start < c.end]


def test_rule4_no_zoom_within_04s_of_transition_and_one_full_frame_fx_at_a_time():
    b = mid("ground.", "emphasis", strength=3)
    cut = round(b.end + 0.2, 3)
    plan = run([b], intensity="unhinged", cuts=(cut,), passage_breaks=(cut,))
    assert cues(plan, "transition")
    for z in cues(plan, "zoom"):
        assert max(0.0, max(z.start, cut) - min(z.end, cut)) >= 0.4 - 1e-6


def test_rule4_full_frame_hits_08s_apart_across_beats():
    w = words_from("Boom one. Boom two. Boom three. Boom four. and then everything went quiet for a while. " * 3)
    beats = [beat(w, f"Boom {n}.", "punchline", strength=3) for n in ("one", "two", "three", "four")]
    plan = run(beats, words=w, intensity="unhinged", template="slam")
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
    assert not run([hook], template="marker_circle", intensity="unhinged", hook_window=window).hook_title_suppressed
    late = mid("wallet", "punchline", strength=3)
    assert not run([late], template="slam", intensity="off", hook_window=window).hook_title_suppressed
    assert not run([hook], template="slam", intensity="off").hook_title_suppressed


def test_rule6_transitions_only_at_cuts_never_inside_a_word():
    inside = next(w for w in WORDS if w["text"] == "wallet")
    word_cut = round((inside["start"] + inside["end"]) / 2, 3)
    gap_cut = round((WORDS[20]["end"] + WORDS[21]["start"]) / 2, 3)
    plan = run([], intensity="unhinged", cuts=(word_cut, gap_cut, 30.0), passage_breaks=())
    times = [c.start for c in cues(plan, "transition")]
    assert word_cut not in times
    assert set(times) <= {gap_cut, 30.0}
    assert all(c.id.startswith("t") and c.id.endswith(".tr") for c in cues(plan, "transition"))


def test_rule6_chill_only_on_passage_breaks_and_ranked_by_strongest_beat():
    cuts = (8.0, 16.0, 24.0)
    plan = run([], intensity="chill", cuts=cuts, passage_breaks=())
    assert not cues(plan, "transition")
    plan = run([], intensity="chill", cuts=cuts, passage_breaks=(16.0, 24.0))
    assert len(cues(plan, "transition")) == 1
    strong = mid("He drove over", "payoff", strength=3)
    near = round(strong.start - 0.2, 3)          # in the sentence gap before the payoff
    plan = run([strong], intensity="chill", cuts=(9.0, near), passage_breaks=(9.0, near))
    # chill allows one transition; the passage break near the strongest beat wins
    trs = cues(plan, "transition")
    assert len(trs) == 1 and trs[0].start == near


def test_rule7_ramp_never_touches_hook_window_swear_or_bleep():
    build = mid(BUILD, "build", strength=3)
    assert cues(run([build], intensity="spicy"), "speed")
    assert not cues(run([build], intensity="spicy", hook_window=(0.12, build.start + 0.5)), "speed")
    swear = mid("the number", "swear", after=next(i for i, w in enumerate(WORDS) if w["text"] == "called"))
    assert not cues(run([build, swear], intensity="spicy"), "speed")


def test_rule7_freeze_sits_at_word_end_not_inside_speed():
    beats, kw = payoff_scene()
    for seed in range(25):
        plan = run(beats, intensity="unhinged", seed=seed, **kw)
        for f in cues(plan, "freeze"):
            assert not any(w["start"] < f.start < w["end"] for w in WORDS)
            nxt = min((w["start"] for w in WORDS if w["start"] >= f.start), default=plan.edit_duration)
            assert nxt - f.start >= 0.3 - 1 / 30
            assert not any(s.start < f.start < s.end for s in cues(plan, "speed"))


def test_rule7_warp_caps():
    tight = run(*payoff_scene()[:1], intensity="unhinged", max_clip_seconds=DUR - 2.2)
    assert not cues(tight, "freeze")                   # final would exceed max_clip_seconds + 2
    for name in ("story", "talking_head"):
        inp, seed = load_fixture(name)
        plan = resolve(inp, EditStyle(intensity="unhinged"), "default", seed)
        holds = sum(c.hold for c in cues(plan, "freeze"))
        assert holds <= min(1.6, 0.04 * plan.edit_duration) + 1e-6
        delta = sum(abs((c.end - c.start) / c.rate - (c.end - c.start)) for c in cues(plan, "speed"))
        assert delta <= 0.1 * plan.edit_duration + 0.05


def test_rule7_no_warp_when_speed_off_or_word_timing_missing():
    beats, kw = payoff_scene()
    beats.append(mid(BUILD, "build", strength=3))
    assert not cues(run(beats, style=EditStyle(intensity="unhinged", speed=False), **kw), "speed")
    plan = run(beats, intensity="unhinged", meta={"timing": "approx"}, **kw)
    assert not cues(plan, "speed") and not cues(plan, "freeze")


def test_rule8_resolver_never_emits_kenburns():
    for name in ("story", "talking_head", "no_timing"):
        inp, seed = load_fixture(name)
        for template in TEMPLATES:
            for intensity in INTENSITIES:
                plan = resolve(inp, EditStyle(intensity=intensity), template, seed)
                assert not [c for c in plan.cues if c.id.startswith("kb")]


def test_rule9_music_mood_bed_and_levels():
    plan = run([mid("wallet", "emphasis")], intensity="spicy", meta={"mood": "funny"})
    assert (plan.music.mood, plan.music.bed, plan.music.gain_db, plan.music.duck_db) == ("funny", "funny", -20, -12)
    assert run([], intensity="chill").music.gain_db == -24
    assert run([], intensity="unhinged").music.gain_db == -17
    plan = run([], style=EditStyle(intensity="spicy", music_mood="dark"))
    assert plan.music.mood == "dark"
    assert run([], style=EditStyle(intensity="spicy", music=False)).music is None
    assert run([], template="slam", intensity="off").music is None


def test_rule9_loop_ending_offsets_the_bed_to_the_seam():
    plan = run([], style=EditStyle(intensity="spicy", loop_ending=True))
    length = 61.935
    assert plan.music.offset == pytest.approx((length - plan.edit_duration % length) % length, abs=1e-3)
    assert plan.music.fade_in == plan.music.fade_out == 0.02
    assert plan.loop_ending == "crossfade"


def test_rule10_retention_grade_and_grain():
    assert run([], intensity="chill").grade == "none"
    assert run([], intensity="spicy").grade == "punchy"
    assert run([], intensity="unhinged").grade == "punchy"
    assert run([], intensity="unhinged", meta={"mood": "dramatic"}).grade == "teal_orange"
    assert run([], style=EditStyle(intensity="chill", grade="vhs")).grade == "vhs"
    assert [run([], style=EditStyle(intensity=i, grain=True)).grain for i in ("chill", "spicy", "unhinged")] == [
        0.15, 0.2, 0.3]
    plan = run([], style=EditStyle(intensity="spicy", progress_bar=True))
    assert plan.progress_bar and plan.loop_ending == "none"


def test_sfx_bus_gain_row_and_cue_gains_stay_relative():
    assert [sfx_bus_gain_db(EditStyle(intensity=i), "normal") for i in INTENSITIES] == [0.0, -4.0, 0.0, 2.0]
    assert sfx_bus_gain_db(EditStyle(intensity="unhinged"), "serious") == -4.0
    plan = run([mid(HOOK, "hook", strength=3), mid("wallet", "emphasis", strength=3)], intensity="unhinged")
    assert all(c.gain_db <= 0 for c in cues(plan, "sfx"))       # per-sound trims only


def test_rule11_reactions_opt_in_and_available():
    b = mid("hugged us both", "punchline", strength=3)
    meta = {"reactions": ({"at": b.start, "query": "lol"}, {"at": 5.0, "query": "wow"})}
    on = EditStyle(intensity="spicy", reactions=True)
    assert len(cues(run([b], style=on, meta=meta, reactions_available=True), "reaction")) == 1
    assert not cues(run([b], style=on, meta=meta, reactions_available=False), "reaction")
    assert not cues(run([b], intensity="spicy", meta=meta, reactions_available=True), "reaction")
    two = cues(run([b], style=EditStyle(intensity="unhinged", reactions=True), meta=meta, reactions_available=True),
               "reaction")
    assert len(two) == 2 and {c.query for c in two} == {"lol", "wow"}
    blocked = run([b], style=on, meta=meta, reactions_available=True, broll_windows=((b.start - 1, b.start + 3),))
    assert [c.query for c in cues(blocked, "reaction")] == ["wow"]


def test_rule12_user_cues_kept_and_collisions_dropped():
    b = mid("wallet", "emphasis", strength=2)
    user_zoom = ZoomCue(id="u1", start=b.start + 0.1, end=b.start + 0.6, mode="punch", scale=1.3, origin="user")
    user_sfx = SfxCue(id="u2", start=b.start, end=b.start, sound="slap", origin="user")
    plan = run([b], intensity="spicy", user_cues=(user_zoom, user_sfx))
    assert one(plan, "u1") == user_zoom and one(plan, "u2") == user_sfx
    assert not has(plan, "b0.zoom") and not has(plan, "b0.sfx")


def test_rule12_suppressed_ids_never_emitted_and_user_ids_not_duplicated():
    b = mid("wallet", "emphasis", strength=2)
    full = run([b], intensity="spicy")
    assert has(full, "b0.zoom")
    plan = run([b], intensity="spicy", suppressed=frozenset({"b0.zoom"}))
    assert not has(plan, "b0.zoom") and has(plan, "b0.sfx")
    moved = ZoomCue(id="b0.zoom", start=20.0, end=20.5, mode="punch", scale=1.2, origin="user")
    plan = run([b], intensity="spicy", user_cues=(moved,))
    assert [c for c in plan.cues if c.id == "b0.zoom"] == [moved]


# ============================================================================= §8.6 modes


def test_style_only_accents_and_intrinsics_no_music_zoom_or_warp():
    beats = [mid(HOOK, "hook", strength=3), mid("wallet", "emphasis", strength=3),
             mid("a long time.", "payoff", strength=3), mid(BUILD, "build", strength=3)]
    for template in FX_TEMPLATES:
        plan = run(beats, template=template, intensity="off", cuts=(15.0,), passage_breaks=(15.0,))
        kinds = {c.kind for c in plan.cues}
        assert kinds <= {"accent", "sfx", "flash", "desaturate", "bleep"}, (template, kinds)
        assert plan.music is None and plan.grade == "none" and not plan.progress_bar
        sfx_ids = [c.id for c in cues(plan, "sfx")]
        assert all(i.split(".")[1] in ("accent", "slam", "behind", "burst") for i in sfx_ids), sfx_ids
    assert cues(run(beats, template="slam", intensity="off"), "flash")
    assert cues(run(beats, template="kinetic_stack", intensity="off"), "desaturate")


def test_legacy_template_with_intensity_has_no_accents():
    beats = [mid(HOOK, "hook", strength=3), mid("wallet", "emphasis", strength=3)]
    for legacy in ("default", "hormozi", "mrbeast", "minimal", "tiktok", "neon", "podcast"):
        plan = run(beats, template=legacy, intensity="unhinged")
        assert not cues(plan, "accent") and cues(plan, "zoom")


def test_no_timing_only_global_fx():
    inp, seed = load_fixture("no_timing")
    plan = resolve(inp, EditStyle(intensity="spicy", grain=True, progress_bar=True), "marker_circle", seed)
    assert {c.kind for c in plan.cues} <= {"transition", "sfx"}
    assert all(c.kind == "transition" or c.id.endswith(".tr.sfx") for c in plan.cues)
    assert plan.music.gain_db == -24 and plan.music.duck_db == 0
    assert plan.grade == "punchy" and plan.grain == 0.2 and plan.progress_bar and plan.timing == "none"


# ============================================================================= §8.7 template variants


def test_variant_marker_circle():
    w = words_from("I will never ever do that again because it was a total mess. " * 4)
    plan = run([beat(w, "never", "emphasis"), beat(w, "total mess.", "punchline", after=10),
                beat(w, "again", "emphasis", after=25), beat(w, "it was", "number", after=40, value=1.0)],
               words=w, template="marker_circle", intensity="chill")
    assert [c.variant for c in cues(plan, "accent")] == ["strike", "underline", "circle", "arrow"]


def test_variant_emoji_drop_tone_emoji_and_bursts():
    plan = run([mid("wallet", "emphasis", strength=1, tone="funny")], template="emoji_drop", intensity="spicy")
    accent = one(plan, "b0.accent")
    assert (accent.variant, accent.emoji) == ("emoji", "😂")
    plan = run([mid("wallet", "emphasis", strength=2, emoji="💰")], template="emoji_drop", intensity="unhinged")
    accent = one(plan, "b0.burst")
    assert (accent.variant, accent.emoji, accent.value) == ("emoji_burst", "💰", 8.0)
    assert one(run([mid("wallet", "emphasis", strength=3)], template="emoji_drop", intensity="chill"),
               "b0.accent").variant == "emoji"


def test_variant_slam_top_strength3_and_intrinsic_visuals():
    beats = [mid(HOOK, "hook", strength=3), mid("wallet", "emphasis", strength=3),
             mid("a long time.", "payoff", strength=3)]
    plan = run(beats, template="slam", intensity="spicy")
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
    plan = run(beats, words=w, template="behind_head", intensity="unhinged")
    behind = [c for c in cues(plan, "accent") if c.variant == "behind"]
    assert 1 <= len(behind) <= 3 and all(c.end - c.start <= 3.0 + 1e-6 for c in behind)
    assert all(len(c.text.split()) <= 2 for c in behind)


def test_variant_stat_counter():
    plan = run([mid("a phone number", "number", value=12.0, prefix="$", suffix="k"),
                mid("last year.", "number", value=None), mid("wallet", "emphasis")],
               template="stat_counter", intensity="chill")
    by_beat = {c.beat: c for c in cues(plan, "accent")}
    variants = sorted(c.variant for c in by_beat.values())
    assert variants == ["arrow", "counter", "highlight"]
    counter = next(c for c in by_beat.values() if c.variant == "counter")
    assert (counter.value, counter.prefix, counter.suffix) == (12.0, "$", "k")


def test_variant_sticker_slap_label_or_short_words():
    plan = run([mid("wallet", "emphasis", label="NO WAY")], template="sticker_slap", intensity="off")
    assert one(plan, "b0.accent").text == "NO WAY"
    plan = run([mid("nobody really wanted to be outside", "emphasis")], template="sticker_slap", intensity="off")
    sticker = one(plan, "b0.accent")
    assert sticker.variant == "sticker" and 1 <= len(sticker.text.split()) <= 3
    assert sticker.end - sticker.start <= 1.6 + 1e-6


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


def test_variant_decode_glitch_spicy_adds_visual_glitch():
    assert not cues(run([mid("wallet", "emphasis")], template="decode_glitch", intensity="chill"), "glitch")
    plan = run([mid("wallet", "emphasis")], template="decode_glitch", intensity="spicy")
    glitch = one(plan, "b0.accent.glitch")
    assert glitch.end - glitch.start == pytest.approx(0.08, abs=1e-3)


def test_variant_highlighter_swipe():
    plan = run([mid("wallet", "emphasis"), mid("a phone number", "number", value=1.0),
                mid("hugged us both", "punchline")], template="highlighter_swipe", intensity="spicy")
    assert sorted(c.beat for c in cues(plan, "accent")) == ["b0", "b1"]
    wild = run([mid("wallet", "emphasis")], template="highlighter_swipe", intensity="unhinged")
    extra = [c for c in cues(wild, "accent") if c.id.startswith("w")]
    assert extra and all(sum(ch.isalpha() for ch in c.text) >= 4 for c in extra)


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
        a = resolve(inp, EditStyle(intensity="unhinged"), template, seed)
        b = resolve(inp, EditStyle(intensity="unhinged"), template, seed)
        assert a == b
    assert resolve(inp, EditStyle(intensity="spicy"), "slam", seed) != resolve(
        inp, EditStyle(intensity="spicy"), "slam", seed + 1)


@pytest.mark.parametrize("name", ["story", "talking_head"])
def test_removing_one_beat_leaves_other_cues_unchanged(name):
    inp, seed = load_fixture(name)
    for template in ("marker_circle", "slam", "stat_counter", "face_doodles", "default"):
        for intensity in ("chill", "spicy", "unhinged"):
            full = {c.id: c for c in resolve(inp, EditStyle(intensity=intensity), template, seed).cues}
            for drop in inp.beats.beats:
                beats = inp.beats.model_copy(update={"beats": tuple(b for b in inp.beats.beats if b.id != drop.id)})
                smaller = ResolveInput(**{**inp.__dict__, "beats": beats})
                for cue in resolve(smaller, EditStyle(intensity=intensity), template, seed).cues:
                    assert cue.beat != drop.id
                    before = full.get(cue.id)
                    if before is None or before.beat == drop.id:
                        continue           # a cue that is new, or that the removed beat used to own (g.wait)
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


def test_every_sound_id_is_reachable_over_the_goldens():
    heard: set[str] = set()
    for name in ("talking_head", "story", "no_timing"):
        for entry in json.loads((FIXTURES / f"{name}.golden.json").read_text()).values():
            for line in entry["cues"]:
                m = re.search(r" sfx +(\w+)#", line)
                if m:
                    heard.add(m.group(1))
    assert heard == set(SOUND_IDS)


def test_story_after_default_cleanup_warps():
    inp, seed = load_fixture("story")
    assert inp.cuts and inp.passage_breaks           # real cleanup cut long pauses and the 3.6 s gap
    for template in TEMPLATES:
        spicy = resolve(inp, EditStyle(intensity="spicy"), template, seed)
        assert cues(spicy, "speed"), template
        wild = resolve(inp, EditStyle(intensity="unhinged"), template, seed)
        assert cues(wild, "speed") and cues(wild, "freeze"), template


# ============================================================================= review fixes


def test_rule12_suppressing_a_visual_drops_its_intrinsic_children():
    b = mid("a long time.", "payoff", strength=3)
    full = run([b], template="slam", intensity="spicy")
    children = [c.id for c in full.cues if c.id.startswith("b0.slam.")]
    assert has(full, "b0.slam") and children
    plan = run([b], template="slam", intensity="spicy", suppressed=frozenset({"b0.slam"}))
    assert not [c for c in plan.cues if c.id == "b0.slam" or c.id.startswith("b0.slam.")]
    # unrelated cues of the same beat stay exactly as they were
    others = {c.id: c for c in full.cues if not c.id.startswith("b0.slam")}
    assert {c.id: c for c in plan.cues} == others


def test_slam_template_hook_always_slams_any_strength():
    plan = run([mid(HOOK, "hook", strength=1), mid("a long time.", "payoff", strength=3)],
               template="slam", intensity="chill")
    assert one(plan, "b0.slam").variant == "slam"


def test_swear_glitch_needs_a_bleep_to_sit_on():
    b = mid("raining", "swear")
    assert has(run([b], intensity="unhinged"), "b0.glitch")
    assert not has(run([b], style=EditStyle(intensity="unhinged", censor="off")), "b0.glitch")
    assert not has(run([b], intensity="unhinged", suppressed=frozenset({"b0.bleep"})), "b0.glitch")


def test_freeze_is_at_least_03s_before_the_next_word_after_quantising():
    beats, kw = payoff_scene()
    for seed in range(25):
        plan = run(beats, intensity="unhinged", seed=seed, **kw)
        for f in cues(plan, "freeze"):
            nxt = min((w["start"] for w in WORDS if w["start"] >= f.start), default=plan.edit_duration)
            assert nxt - f.start >= 0.3 - 1e-3


def test_long_beat_ids_never_raise():
    b = mid("a phone number", "number", value=12.0, prefix="$").model_copy(update={"id": "u" + "9" * 23})
    plan = run([b], template="stat_counter", intensity="unhinged")
    assert all(len(c.id) <= 32 for c in plan.cues)


def test_loop_music_offset_uses_the_emitted_warp():
    from src.edit_fx.warp import build_warp

    beats, kw = payoff_scene()
    plan = run(beats, style=EditStyle(intensity="unhinged", loop_ending=True), **kw)
    assert cues(plan, "freeze") or cues(plan, "speed")
    out = build_warp(plan.cues, plan.edit_duration).out_duration
    length = 61.935
    assert plan.music.offset == pytest.approx((length - out % length) % length, abs=1e-3)
