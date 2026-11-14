"""Baseline pushes (§8.5 rule 8): the FX path's single slow-zoom source, added once the layout is known."""

from __future__ import annotations

from src.edit_fx.baseline import baseline_pushes
from src.edit_fx.contracts import (
    EditStyle,
    FxPlan,
    ReactionCue,
    SfxCue,
    TransitionCue,
    ZoomCue,
)
from src.edit_fx.resolver import resolve
from src.edit_fx.rng import cue_seed
from tests.fixtures.fx_resolver.builders import TEMPLATES, load_fixture


def plan(duration: float, *cues, seed: int = 99) -> FxPlan:
    return FxPlan(style=EditStyle(intensity="bold"), template="default", seed=seed, edit_duration=duration,
                  cues=tuple(sorted(cues, key=lambda c: (c.start, c.id))))


def kb(p: FxPlan) -> list[ZoomCue]:
    return [c for c in p.cues if c.id.startswith("kb")]


def test_whole_clip_gets_one_gentle_push():
    out = baseline_pushes(plan(20.0), cuts=(), broll_windows=())
    [push] = kb(out)
    assert (push.id, push.start, push.end, push.mode, push.scale, push.anchor) == ("kb0", 0.0, 20.0, "push", 1.05,
                                                                                  "face")
    assert push.seed == cue_seed(99, "kb0", "push") and push.origin == "auto" and push.beat == ""


def test_short_clips_and_short_gaps_get_nothing():
    assert not kb(baseline_pushes(plan(5.9), cuts=(), broll_windows=()))
    zooms = (ZoomCue(id="b0.zoom", start=5.0, end=6.0, mode="punch", scale=1.15),
             ZoomCue(id="b1.zoom", start=11.5, end=12.0, mode="punch", scale=1.15))
    out = baseline_pushes(plan(17.0, *zooms), cuts=(), broll_windows=())
    assert not kb(out)       # gaps: 5.0, 5.5, 5.0 s


def test_fills_every_6s_stretch_between_zooms_in_time_order():
    zooms = (ZoomCue(id="b0.zoom", start=7.0, end=8.0, mode="punch", scale=1.15),
             ZoomCue(id="b1.push", start=10.0, end=12.0, mode="push", scale=1.06))
    out = baseline_pushes(plan(30.0, *zooms), cuts=(), broll_windows=())
    assert [(c.id, c.start, c.end) for c in kb(out)] == [("kb0", 0.0, 7.0), ("kb1", 12.0, 30.0)]


def test_broll_reaction_and_transition_windows_are_excluded():
    cues = (ReactionCue(id="g.react0", start=20.0, end=22.0, query="lol"),
            TransitionCue(id="t0.tr", start=30.0, end=30.0, style="whip"))
    out = baseline_pushes(plan(45.0, *cues), cuts=(), broll_windows=((8.0, 11.0),))
    spans = [(c.start, c.end) for c in kb(out)]
    assert spans == [(0.0, 8.0), (11.0, 20.0), (22.0, 29.6), (30.4, 45.0)]


def test_long_stretch_splits_at_a_cut_so_the_reset_hides():
    out = baseline_pushes(plan(40.0), cuts=(5.0, 21.0, 35.0), broll_windows=())
    assert [(c.start, c.end) for c in kb(out)] == [(0.0, 21.0), (21.0, 40.0)]
    out = baseline_pushes(plan(16.0), cuts=(8.0,), broll_windows=())
    assert [(c.start, c.end) for c in kb(out)] == [(0.0, 16.0)]


def test_idempotent_and_deterministic():
    base = plan(50.0, ZoomCue(id="b0.zoom", start=20.0, end=21.0, mode="punch", scale=1.2),
                SfxCue(id="b0.sfx", start=20.0, end=20.0, sound="pop"))
    once = baseline_pushes(base, cuts=(10.0, 35.0), broll_windows=((40.0, 42.0),))
    assert baseline_pushes(once, cuts=(10.0, 35.0), broll_windows=((40.0, 42.0),)) == once
    assert baseline_pushes(base, cuts=(10.0, 35.0), broll_windows=((40.0, 42.0),)) == once
    assert [c for c in once.cues if not c.id.startswith("kb")] == list(base.cues)
    assert list(once.cues) == sorted(once.cues, key=lambda c: (c.start, c.id))


def test_user_owned_kb_ids_are_kept_and_never_duplicated():
    moved = ZoomCue(id="kb0", start=2.0, end=9.0, mode="push", scale=1.1, origin="user")
    out = baseline_pushes(plan(30.0, moved), cuts=(), broll_windows=())
    ids = [c.id for c in out.cues]
    assert ids.count("kb0") == 1 and out.cues[0] == moved
    assert [(c.id, c.start, c.end) for c in kb(out) if c.origin == "auto"] == [("kb1", 9.0, 30.0)]


def test_adds_only_gentle_motion_to_resolved_plans():
    for name in ("talking_head", "story"):
        inp, seed = load_fixture(name)
        for template in TEMPLATES:
            for intensity in ("off", "subtle", "bold"):
                if template == "default" and intensity == "off":
                    continue
                resolved = resolve(inp, EditStyle(intensity=intensity), template, seed)
                out = baseline_pushes(resolved, cuts=inp.cuts, broll_windows=inp.broll_windows)
                pushes = kb(out)
                assert [c.id for c in pushes] == [f"kb{n}" for n in range(len(pushes))]
                assert all(c.end - c.start >= 6.0 - 1e-6 and c.scale == 1.05 for c in pushes)
                others = [c for c in resolved.cues if c.kind == "zoom"]
                for p in pushes:
                    assert not any(p.start < z.end and z.start < p.end for z in others)
                    for tr in (c for c in resolved.cues if c.kind == "transition"):
                        assert p.end <= tr.start - 0.4 + 1e-6 or p.start >= tr.start + 0.4 - 1e-6
    inp, seed = load_fixture("story")
    style_only = resolve(inp, EditStyle(), "marker_circle", seed)
    assert kb(baseline_pushes(style_only, cuts=inp.cuts, broll_windows=()))   # keeps today's gentle motion
