"""Resolver property tests (§8.3 table rows, §8.5 rules): 200 random clips x 11 templates x 4 intensities.

Every row of the §8.3 budget table is asserted on every plan: caps and min gaps per family, counts per clip,
music levels, grade and grain. The §8.5 global rules (quiet zones, exclusions, hit spacing, warp caps, bleeps,
serious mode) hold everywhere too.
"""

from __future__ import annotations

import random
import re
from itertools import combinations

import pytest

from src.edit_fx.contracts import TRANSITION_HALF, Beat, EditBeats, EditStyle, ReactionIdea
from src.edit_fx.resolver import (
    BUDGETS,
    GRAIN,
    IMPACT_SOUNDS,
    MUSIC_LEVELS,
    SERIOUS_DROP_SOUNDS,
    ResolveInput,
    family_cap,
    resolve,
    tier_for,
    transition_cap,
)
from tests.fixtures.fx_resolver.builders import BED_LENGTHS, BEDS, INTENSITIES, SFX_VARIANTS, TEMPLATES

SEEDS = range(200)
EPS = 1e-6
FRAME = 1 / 30

CONTENT = ("money", "business", "people", "crazy", "amazing", "honestly", "literally", "secret", "million",
           "family", "terrible", "beautiful", "everything", "nothing", "dangerous", "simple", "results", "never",
           "growth", "mistake", "ridiculous", "freedom", "teacher", "weekend", "struggle", "winning", "journey")
FILLER = ("the", "a", "and", "so", "we", "it", "was", "to", "of", "in", "my", "you", "I", "that", "is", "just")
NUMBERS = (("$40,000", 40000.0, "$", ""), ("90%", 90.0, "", "%"), ("12", 12.0, "", ""), ("$2.5M", 2.5, "$", "M"),
           ("three", 3.0, "", ""), ("2019", 2019.0, "", ""))
SWEARS = ("damn", "shit", "fucking", "hell")
KINDS = ("emphasis", "emphasis", "emphasis", "number", "punchline", "swear", "emotional_turn", "build", "payoff")
TONES = ("neutral", "hype", "funny", "shocked", "sad", "angry", "wholesome", "suspense", "awkward", "smug")
MOODS = ("hype", "chill", "dramatic", "funny", "inspiring", "dark")


def random_clip(seed: int) -> tuple[ResolveInput, dict]:
    rng = random.Random(seed * 7919 + 13)
    n = rng.randint(12, 170)
    words: list[dict] = []
    meta: list[dict] = []          # per word: kind of token
    t = rng.uniform(0.0, 0.6)
    sentence_len = rng.randint(4, 12)
    for i in range(n):
        roll = rng.random()
        if roll < 0.06:
            text, info = rng.choice(NUMBERS)[0], {"number": True}
        elif roll < 0.10:
            text, info = rng.choice(SWEARS), {"swear": True}
        elif roll < 0.55:
            text, info = rng.choice(CONTENT), {}
        else:
            text, info = rng.choice(FILLER), {}
        end_sentence = sentence_len <= 1 or i == n - 1
        if end_sentence:
            text += rng.choice(".!?")
            sentence_len = rng.randint(4, 12)
        else:
            sentence_len -= 1
        length = 0.12 + 0.03 * len(text) + rng.uniform(0, 0.12)
        words.append({"id": f"w{i}", "start": round(t, 3), "end": round(t + length, 3), "text": text})
        meta.append({**info, "end": end_sentence})
        gap = rng.choice((0.3, 0.45, 0.6, 0.9, 1.15)) if end_sentence else rng.uniform(0.04, 0.14)
        if rng.random() < 0.05:
            gap = rng.uniform(0.6, 1.2)
        t += length + gap
    duration = round(words[-1]["end"] + rng.uniform(0.2, 1.2), 3)

    timing = rng.choices(("word", "approx"), (0.9, 0.1))[0]
    beats: list[Beat] = []
    if rng.random() < 0.75:
        last = next((i for i, m in enumerate(meta) if m["end"]), len(words) - 1)
        last = min(last, 7)
        beats.append(Beat(id="h", kind="hook", start=words[0]["start"], end=words[last]["end"],
                          text=" ".join(w["text"] for w in words[:last + 1]), strength=rng.randint(1, 3),
                          tone=rng.choice(TONES)))
    for i, m in enumerate(meta):
        if m.get("swear"):
            beats.append(Beat(id="s", kind="swear", start=words[i]["start"], end=words[i]["end"],
                              text=words[i]["text"], strength=2))
        elif m.get("number") and rng.random() < 0.8:
            text, value, prefix, suffix = next(x for x in NUMBERS if words[i]["text"].startswith(x[0]))
            beats.append(Beat(id="n", kind="number", start=words[i]["start"], end=words[i]["end"], text=text,
                              strength=rng.randint(1, 3), value=value if rng.random() < 0.8 else None,
                              prefix=prefix, suffix=suffix, tone=rng.choice(TONES)))
    for _ in range(rng.randint(0, max(1, n // 4))):
        i = rng.randrange(len(words))
        j = min(len(words) - 1, i + rng.randint(0, 5))
        kind = rng.choice(KINDS)
        if kind in ("swear", "number"):
            kind = "emphasis"
        beats.append(Beat(id="x", kind=kind, start=words[i]["start"], end=words[j]["end"],
                          text=" ".join(w["text"] for w in words[i:j + 1])[:200], strength=rng.randint(1, 3),
                          tone=rng.choice(TONES), label=rng.choice(("", "", "WAIT WHAT", "NO WAY")),
                          emoji=rng.choice(("", "", "💰", "🔥"))))
    beats.sort(key=lambda b: (b.start, b.end, b.kind))
    beats = [b.model_copy(update={"id": f"b{k}"}) for k, b in enumerate(beats)]
    hook = next((b for b in beats if b.kind == "hook"), None)
    reactions = tuple(ReactionIdea(at=rng.choice(words)["start"], query=rng.choice(("lol", "shocked cat", "wow")))
                      for _ in range(rng.randint(0, 3)))
    eb = EditBeats(words_hash="p", mood=rng.choice(MOODS),
                   sensitivity=rng.choices(("normal", "serious"), (0.88, 0.12))[0], timing=timing,
                   hook_end=hook.end if hook else None, beats=tuple(beats), reactions=reactions)

    gaps = [round((a["end"] + b["start"]) / 2, 3) for a, b, m in zip(words, words[1:], meta) if m["end"]]
    cuts = tuple(sorted(c for c in gaps if rng.random() < 0.4))
    if rng.random() < 0.15 and len(words) > 4:
        w = rng.choice(words)
        cuts = tuple(sorted({*cuts, round((w["start"] + w["end"]) / 2, 3)}))    # a junction inside a word
    breaks = tuple(c for c in cuts if rng.random() < 0.4)
    broll = []
    for _ in range(rng.randint(0, 2)):
        a = rng.uniform(0, max(0.1, duration - 3))
        broll.append((round(a, 3), round(min(duration, a + rng.uniform(1.5, 4)), 3)))
    hook_window = (0.12, round(min(4.0, max(1.5, duration - 0.25)), 3)) if rng.random() < 0.7 else None
    max_clip = rng.choice((None, duration + 0.5, duration + 5, 90.0))
    inp = ResolveInput(words=tuple(words), beats=eb, duration=duration, cuts=cuts, passage_breaks=breaks,
                       broll_windows=tuple(sorted(broll)), hook_window=hook_window, max_clip_seconds=max_clip,
                       sfx_variants=SFX_VARIANTS, beds=BEDS, bed_lengths=BED_LENGTHS,
                       reactions_available=rng.random() < 0.6)
    options = {
        "sfx": rng.random() < 0.9, "music": rng.random() < 0.85, "zooms": rng.random() < 0.9,
        "shake": rng.random() < 0.9, "transitions": rng.random() < 0.9, "speed": rng.random() < 0.9,
        "censor": rng.choice(("off", "bleep", "bleep_bar", "bleep_pixelate", "bleep_bar")),
        "grain": rng.random() < 0.3, "progress_bar": rng.random() < 0.3, "loop_ending": rng.random() < 0.25,
        "wait_for_it": rng.random() < 0.85, "reactions": rng.random() < 0.5,
    }
    return inp, options


# ----------------------------------------------------------------------------- checks


def cap(family: str, tier: str, dur: float) -> int:
    return family_cap(BUDGETS[family][tier], dur)


def gap(family: str, tier: str) -> float:
    return BUDGETS[family][tier][2]


def owner(c) -> str:
    return c.beat or c.id


def spaced(times_owners, min_gap):
    for (t1, o1), (t2, o2) in combinations(times_owners, 2):
        if o1 != o2 and abs(t1 - t2) < min_gap - EPS:
            return False
    return True


def overlaps(a0, a1, b0, b1):
    return a0 < b1 - EPS and b0 < a1 - EPS


def in_window(c, windows, half=0.0):
    s, e = (c.start - half, c.start + half) if half else (c.start, c.end)
    for a, b in windows:
        if e - s < EPS:
            if a - EPS <= s <= b + EPS:
                return True
        elif overlaps(s, e, a, b):
            return True
    return False


def check_plan(inp: ResolveInput, style: EditStyle, template: str, plan) -> None:
    dur = inp.duration
    tier = tier_for(style, inp.beats.sensitivity)
    cues = plan.cues
    beats = {b.id: b for b in inp.beats.beats}
    ids = [c.id for c in cues]
    assert len(ids) == len(set(ids))
    assert list(cues) == sorted(cues, key=lambda c: (c.start, c.id))
    assert all(0 <= c.start <= c.end <= dur + EPS for c in cues)
    assert not [c for c in cues if re.fullmatch(r"kb\d+", c.id)]
    by_kind: dict[str, list] = {}
    for c in cues:
        by_kind.setdefault(c.kind, []).append(c)
    get = lambda k: by_kind.get(k, [])  # noqa: E731

    # --- accents (template marks): cap + gap; one mark per beat (siblings count once); none for legacy templates
    accents = get("accent")
    if template == "default":
        assert not accents
    groups: dict[str, float] = {}
    for c in accents:
        groups[owner(c)] = min(groups.get(owner(c), c.start), c.start)
    assert len(groups) <= cap("accent", tier, dur)
    assert spaced([(t, o) for o, t in groups.items()], gap("accent", tier))

    # --- zooms
    punches = [c for c in get("zoom") if c.mode == "punch"]
    pushes = [c for c in get("zoom") if c.mode != "punch"]
    assert len(punches) <= cap("punch", tier, dur)
    assert spaced([(c.start, c.id) for c in punches], gap("punch", tier))
    assert len(pushes) <= cap("push", tier, dur)
    if tier == "style" or not style.zooms:
        assert not get("zoom")

    # --- shake: cap/gap, only with an impact-class SFX or a slam of the same beat
    shakes = get("shake")
    assert len(shakes) <= cap("shake", tier, dur)
    assert spaced([(c.start, c.id) for c in shakes], gap("shake", tier))
    if not style.shake:
        assert not shakes
    for s in shakes:
        slam = any(a.variant == "slam" and a.beat == s.beat for a in accents)
        impact = any(x.sound in IMPACT_SOUNDS and x.beat == s.beat and abs(x.start - s.start) < 0.05
                     for x in get("sfx"))
        assert slam or impact or not style.sfx

    # --- SFX: cap (a counter's ticker counts with its stamp), gap between different beats, intrinsic-only in style
    sfx = get("sfx")
    counted = [c for c in sfx if c.sound != "ticker"]
    assert len(counted) <= cap("sfx", tier, dur)
    assert spaced([(c.start, owner(c)) for c in sfx], gap("sfx", tier))
    if not style.sfx:
        assert not sfx
    if tier == "style":
        assert all(re.search(r"\.(accent2?|slam|behind|burst)\.sfx2?$|^g\.kinetic\.sfx$", c.id) for c in sfx)
    if inp.beats.sensitivity == "serious":
        assert not {c.sound for c in sfx} & SERIOUS_DROP_SOUNDS
        assert not [c for c in accents if c.variant in ("emoji", "emoji_burst", "doodle")]
        assert not get("reaction") and not get("freeze") and not get("speed")

    # --- transitions: only at cuts, never inside a word, per-tier count, chill on passage breaks only
    trs = get("transition")
    assert len(trs) <= (transition_cap(tier, dur) if style.transitions else 0)
    for c in trs:
        assert any(abs(c.start - cut) < 1e-3 for cut in inp.cuts)
        assert not any(w["start"] + 0.05 < c.start < w["end"] - 0.05 for w in inp.words)
        if tier == "chill":
            assert any(abs(c.start - p) < 1e-3 for p in inp.passage_breaks)

    # --- flash / glitch hits (non-intrinsic), full-frame spacing, one full-frame effect at a time
    hits_fg = [c for c in get("flash") + get("glitch") if not c.id.endswith(".slam.flash")]
    assert len(hits_fg) <= cap("hit", tier, dur)
    if tier == "unhinged":
        assert spaced([(c.start, c.id) for c in hits_fg], gap("hit", tier))
    full_frame = [(c.start, owner(c)) for c in get("flash") + get("glitch") + trs + shakes]
    full_frame += [(c.start, owner(c)) for c in accents if c.variant == "slam"]
    assert spaced(full_frame, 0.8)
    windows = [(c.start, c.end) for c in get("flash") + get("glitch")]
    windows += [(c.start - TRANSITION_HALF[c.style], c.start + TRANSITION_HALF[c.style]) for c in trs]
    for (a0, a1), (b0, b1) in combinations(windows, 2):
        assert not overlaps(a0, a1, b0, b1)

    # --- warp families and caps (G14)
    freezes, speeds = get("freeze"), get("speed")
    assert len(freezes) <= cap("freeze", tier, dur)
    if tier == "spicy" and freezes:
        assert dur >= 15.0
    ramps = [c for c in speeds if c.id.endswith(".ramp")]
    fast = [c for c in speeds if ".fast" in c.id]
    slow = [c for c in speeds if c.id.endswith(".slow")]
    assert len(ramps) <= cap("ramp", tier, dur) and len(fast) <= cap("fast", tier, dur)
    assert len(slow) <= cap("slow", tier, dur)
    assert all(1.15 - EPS <= c.rate <= 1.35 + EPS for c in ramps)
    assert all(1.5 - EPS <= c.rate <= 2.5 + EPS for c in fast)
    assert all(c.rate == 0.5 for c in slow)
    if speeds or freezes:
        assert style.speed and inp.beats.timing == "word" and tier in ("spicy", "unhinged")
    hold = sum(c.hold for c in freezes)
    assert hold <= min(1.6, 0.04 * dur) + EPS
    delta = sum(round((c.end - c.start) / c.rate * 30) / 30 - (c.end - c.start) for c in speeds)
    assert sum(abs(round((c.end - c.start) / c.rate * 30) / 30 - (c.end - c.start)) for c in speeds) <= 0.1 * dur + EPS
    if inp.max_clip_seconds is not None and (hold > 0 or delta > 0):
        assert dur + hold + delta <= inp.max_clip_seconds + 2 + 1e-3
    for a, b in combinations(speeds, 2):
        assert not overlaps(a.start, a.end, b.start, b.end)
    for f in freezes:
        assert not any(s.start + EPS < f.start < s.end - EPS for s in speeds)
        assert not any(w["start"] + EPS < f.start < w["end"] - EPS for w in inp.words)
    for c in ramps:   # speech ramps never carry a swear or a caption-hiding accent and never touch the hook
        assert not any(b.kind == "swear" and overlaps(c.start, c.end, b.start, b.end) for b in beats.values())
        assert not any(a.variant in ("slam", "kinetic") and overlaps(c.start, c.end, a.start, a.end)
                       for a in accents)
        if inp.hook_window:
            assert c.start > inp.hook_window[1] or c.end < inp.hook_window[0]

    # --- other per-clip counts
    assert len([c for c in accents if c.variant == "emoji_burst"]) <= cap("burst", tier, dur)
    assert len(get("reaction")) <= cap("reaction", tier, dur)
    if get("reaction"):
        assert style.reactions and inp.reactions_available
    stickers = [c for c in get("sticker") if c.sticker == "wait_for_it"]
    assert len(stickers) <= cap("wait", tier, dur)
    for w in stickers:
        pay = beats[w.beat]
        lead = 6.0 if tier == "spicy" else 4.0
        assert pay.kind == "payoff" and pay.start - (inp.beats.hook_end or 0.0) >= lead - EPS
        if tier == "spicy":
            assert dur >= 20.0

    # --- quiet zones (rule 3)
    for c in cues:
        if c.kind == "bleep":
            continue
        hook_bound = (c.beat and beats[c.beat].kind == "hook") or c.id.startswith("g.kinetic")
        assert c.start >= 0.25 - EPS or hook_bound, c.id
        if plan.loop_ending == "none":
            assert c.start <= dur - 0.3 + EPS and c.end <= dur - 0.3 + EPS, c.id

    # --- exclusions (rule 4)
    excl = list(inp.broll_windows) + [(c.start, c.end) for c in get("reaction")]
    for c in cues:
        if c.kind in ("zoom", "shake", "glitch") or (c.kind == "accent" and c.variant in ("behind", "doodle")):
            assert not in_window(c, excl), c.id
        if c.kind == "transition":
            assert not in_window(c, excl, TRANSITION_HALF[c.style]), c.id
    for z in get("zoom"):
        for tr in trs:
            assert max(0.0, max(z.start, tr.start) - min(z.end, tr.start)) >= 0.4 - EPS

    # --- bleeps (rule 2)
    swears = [b for b in beats.values() if b.kind == "swear"]
    bleeps = get("bleep")
    if style.active and style.censor != "off" and inp.beats.timing == "word":
        assert {c.beat for c in bleeps} == {b.id for b in swears if b.start < dur}
        cover = {"bleep": "none", "bleep_bar": "bar", "bleep_pixelate": "pixelate"}[style.censor]
        assert all(c.cover == cover and c.tone for c in bleeps)
    else:
        assert not bleeps

    # --- music, grade, grain, retention (§8.3 rows, rules 9-10)
    if tier == "style" or not style.music:
        assert plan.music is None
    elif plan.music is not None:
        gain, duck = MUSIC_LEVELS[tier]
        if inp.beats.timing == "none":
            gain, duck = -24.0, 0.0
        assert (plan.music.gain_db, plan.music.duck_db) == (gain, duck)
        assert plan.music.bed in BEDS[plan.music.mood]
    if tier == "style":
        assert plan.grade == "none" and plan.grain == 0 and not plan.progress_bar
    else:
        expected = {"chill": "none", "spicy": "punchy",
                    "unhinged": "teal_orange" if inp.beats.mood in ("dramatic", "dark") else "punchy"}[tier]
        assert plan.grade == expected
        assert plan.grain == (GRAIN[tier] if style.grain else 0.0)
    if template == "kinetic_stack":
        assert plan.hook_title_suppressed


@pytest.mark.parametrize("block", range(8))
def test_budget_table_and_global_rules_hold_for_every_clip(block):
    for seed in SEEDS[block::8]:
        inp, options = random_clip(seed)
        for template in TEMPLATES:
            for intensity in INTENSITIES:
                if template == "default" and intensity == "off":
                    continue
                style = EditStyle(intensity=intensity, **options)
                plan = resolve(inp, style, template, seed * 31 + 7)
                try:
                    check_plan(inp, style, template, plan)
                except AssertionError as exc:
                    raise AssertionError(f"seed={seed} template={template} intensity={intensity}: {exc}") from exc


@pytest.mark.parametrize("block", range(4))
def test_resolve_is_deterministic(block):
    for seed in SEEDS[block::20]:
        inp, options = random_clip(seed)
        for template in ("slam", "face_doodles", "default"):
            style = EditStyle(intensity="unhinged", **options)
            assert resolve(inp, style, template, seed) == resolve(inp, style, template, seed)


def test_intensity_grows_monotonically_on_average():
    """Each column builds on the one to its left: more cues at higher intensity across many clips."""
    totals = {i: 0 for i in INTENSITIES}
    for seed in SEEDS[::5]:
        inp, _ = random_clip(seed)
        for intensity in INTENSITIES:
            totals[intensity] += len(resolve(inp, EditStyle(intensity=intensity), "marker_circle", seed).cues)
    assert totals["off"] < totals["chill"] < totals["spicy"] < totals["unhinged"]
