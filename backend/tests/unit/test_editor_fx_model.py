"""Dynamic edits in the studio document (§5.1, §5.5, §14.1): the model, the E mapping, re-resolve, the render hook
and how prepare/upgrade seed a draft from a generated clip's plan."""

import json
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import editor_document
from src.edit_fx import resolver
from src.edit_fx.contracts import FX_KINDS, EditStyle, FxPlan
from src.editor_document import (
    DocFx,
    EditDocument,
    NeedsPlan,
    document_fx_on,
    document_needs_fx,
    fx_hold_budget,
    mapped_fx,
    mapped_hold_total,
    resolve_document_fx,
    studio_hook_window,
    studio_junctions,
    turns_fx_on,
)
from src.services import editor_service
from src.services.editor_service import (
    caption_looks,
    doc_fx_from_sidecar,
    generated_emphasis,
    layout_filter_has_zoompan,
    plan_accent_words,
    upgrade_document,
)
from tests.fixtures.editor_store import stored_state

PINS = Path(__file__).resolve().parents[1] / "fixtures" / "legacy_pins"
BOLD = EditStyle(intensity="bold")

TEXT = ("so here is the thing nobody tells you about money you can make ten thousand dollars a month "
        "and still be broke because the real secret is what you keep not what you earn").split()


def words(count: int = 120, step: float = 0.5) -> list[dict]:
    return [
        {"id": f"w{i}", "start": round(i * step, 3), "end": round(i * step + step * 0.8, 3),
         "text": TEXT[i % len(TEXT)]}
        for i in range(count)
    ]


def beats_every(seconds: float, until: float, kind: str = "emphasis", strength: int = 3) -> list[dict]:
    out, t, n = [], 1.0, 0
    while t + 0.8 <= until:
        out.append({"id": f"b{n}", "kind": kind, "start": t, "end": t + 0.8, "text": "money", "strength": strength,
                    "words": ["money"]})
        t += seconds
        n += 1
    return out


def doc(segments, *, fx=None, **extra) -> EditDocument:
    data = {"segments": [{"id": f"s{i}", "start": a, "end": b} for i, (a, b) in enumerate(segments)],
            "words": words(), **extra}
    if fx is not None:
        data["fx"] = fx
    return EditDocument.model_validate(data)


def planned(**fx) -> dict:
    return {"mood": "hype", "beats": beats_every(2.0, 59.0), **fx}


# --------------------------------------------------------------------------- the model


def test_a_document_without_dynamic_edits_dumps_byte_identically_to_the_legacy_pin():
    raw = json.loads((PINS / "editor_document.json").read_text(encoding="utf-8"))
    for value in (raw, {**raw, "fx": None}):
        model = EditDocument.model_validate(value)
        assert "fx" not in model.model_dump()
        assert json.dumps(model.model_dump(mode="json"), ensure_ascii=False) + "\n" == (
            PINS / "expected" / "editor_document_fixture.txt").read_text(encoding="utf-8")
        assert model.model_dump_json() + "\n" == (
            PINS / "expected" / "editor_document_fixture_json.txt").read_text(encoding="utf-8")


def test_null_fx_is_the_same_as_no_fx():
    base = {"segments": [{"id": "s", "start": 0, "end": 2}]}
    assert EditDocument.model_validate({**base, "fx": None}) == EditDocument.model_validate(base)
    assert EditDocument.model_validate({**base, "fx": None}).fx is None


def test_every_cue_kind_round_trips_in_the_document():
    cues = [
        {"kind": "accent", "id": "a", "start": 1, "end": 2, "variant": "circle", "text": "money"},
        {"kind": "zoom", "id": "z", "start": 1, "end": 1.5, "mode": "punch", "scale": 1.2},
        {"kind": "shake", "id": "sh", "start": 2, "end": 2.3, "amp": 10},
        {"kind": "flash", "id": "f", "start": 3, "end": 3.1},
        {"kind": "glitch", "id": "g", "start": 3.5, "end": 3.6, "amount": 0.5},
        {"kind": "desaturate", "id": "d", "start": 4, "end": 5, "amount": 0.7},
        {"kind": "transition", "id": "t", "start": 5, "end": 5, "style": "whip"},
        {"kind": "sfx", "id": "x", "start": 5, "end": 5, "sound": "whoosh"},
        {"kind": "bleep", "id": "bl", "start": 6, "end": 6.4},
        {"kind": "speed", "id": "sp", "start": 7, "end": 8, "rate": 1.3},
        {"kind": "freeze", "id": "fr", "start": 9, "end": 9, "hold": 0.5, "label": "wait"},
        {"kind": "sticker", "id": "st", "start": 10, "end": 11, "sticker": "text", "text": "no way"},
        {"kind": "reaction", "id": "r", "start": 12, "end": 14, "query": "shocked"},
    ]
    document = doc([(0, 60)], fx={"style": {"intensity": "bold"}, "cues": cues, "grade": "warm", "progressBar": True})
    dumped = document.model_dump(mode="json")
    assert dumped["fx"]["version"] == "fx-v1"
    assert [c["kind"] for c in dumped["fx"]["cues"]] == [c["kind"] for c in cues]
    assert EditDocument.model_validate(json.loads(json.dumps(dumped))) == document
    document.validate_duration(60)


@pytest.mark.parametrize("fx, message", [
    ({"beats": [{"id": "b0", "kind": "hook", "start": 1, "end": 70}]}, "tagged moment"),
    ({"cues": [{"kind": "flash", "id": "f", "start": 59, "end": 61}]}, "effect extends"),
    ({"cues": [{"kind": "flash", "id": "f", "start": 1, "end": 2}, {"kind": "flash", "id": "f", "start": 3, "end": 4}]},
     "unique"),
    ({"beats": [{"id": "b", "kind": "hook", "start": 1, "end": 2}, {"id": "b", "kind": "payoff", "start": 3, "end": 4}]},
     "unique"),
    ({"cues": [{"kind": "speed", "id": "a", "start": 1, "end": 3, "rate": 1.2},
               {"kind": "speed", "id": "b", "start": 2.5, "end": 4, "rate": 1.2}]}, "overlap"),
])
def test_validate_duration_checks_dynamic_edits(fx, message):
    with pytest.raises(ValueError, match=message):
        doc([(0, 60)], fx=fx).validate_duration(60)


def test_the_freeze_hold_budget_is_not_a_document_rule():
    """A combined clip rebases several clips' freezes; it must stay a valid draft (AR-M14)."""
    freezes = [{"kind": "freeze", "id": f"f{i}", "start": 1 + i, "end": 1 + i, "hold": 1.6} for i in range(3)]
    document = doc([(0, 10)], fx={"cues": freezes}).validate_duration(60)
    assert mapped_hold_total(document) == pytest.approx(4.8)
    assert fx_hold_budget(10) == pytest.approx(0.4) and fx_hold_budget(100) == pytest.approx(1.6)


def test_mapped_fx_follows_cuts_repeats_and_switched_off_kinds():
    cues = [
        {"kind": "sfx", "id": "s", "start": 2, "end": 2, "sound": "pop"},
        {"kind": "flash", "id": "f", "start": 4, "end": 6},
        {"kind": "zoom", "id": "z", "start": 8, "end": 9, "mode": "punch", "scale": 1.2},
    ]
    document = doc([(0, 5), (0, 5), (8, 10)], fx={"cues": cues, "disabled": ["zoom"]})
    mapped = mapped_fx(document)
    assert [(c.id, c.start, c.end) for c in mapped] == [
        ("s", 2, 2), ("f", 4, 5), ("s@1", 7, 7), ("f@1", 9, 10),
    ]


def test_document_needs_fx_and_turning_it_on():
    legacy = doc([(0, 10)])
    inert = doc([(0, 10)], fx={"style": {"intensity": "off"}, "disabled": list(FX_KINDS)})
    assert not document_needs_fx(legacy) and not document_needs_fx(inert)
    styled = doc([(0, 10)], captions={"template": "slam"})
    assert document_needs_fx(styled)
    music = doc([(0, 10)], fx={"music": {"mood": "hype", "bed": "hype"}})
    assert document_needs_fx(music)
    active_without_cues = doc([(0, 10)], fx={"style": {"intensity": "subtle"}})
    assert not document_needs_fx(active_without_cues) and document_fx_on(active_without_cues)
    assert turns_fx_on(legacy, styled) and turns_fx_on(None, active_without_cues)
    assert not turns_fx_on(styled, styled) and not turns_fx_on(styled, legacy)
    assert not turns_fx_on(active_without_cues, doc([(0, 10)], fx={"style": {"intensity": "max"}}))


def test_studio_hook_window_matches_the_generation_formula():
    from src.media.common import hook_title_window

    for duration in (1.0, 1.5, 3.0, 4.0, 4.3, 30.0):
        window = studio_hook_window(doc([(0, 60)], hookTitle="The secret"), duration)
        if duration <= 1.5:
            assert window == (0.0, max(0.5, duration))
        else:
            assert window == (0.12, hook_title_window(duration))
    assert studio_hook_window(doc([(0, 60)]), 30.0) is None
    timed = doc([(0, 60)], hookTitle="x", hookTiming={"start": 1, "end": 3})
    assert studio_hook_window(timed, 30.0) == (1, 3)


def test_junctions_mark_passage_breaks_for_jumps_and_reorders():
    cuts, breaks = studio_junctions([("a", 0, 5), ("b", 6, 10), ("c", 20, 25), ("d", 1, 2)])
    assert cuts == (5, 9, 14)
    assert breaks == (9, 14)


# --------------------------------------------------------------------------- resolve in E (§5.5)


def _e_cues(document, kind=None):
    return [c for c in mapped_fx(document) if kind is None or c.kind == kind]


def test_needs_plan_when_an_active_style_has_no_beats():
    with pytest.raises(NeedsPlan):
        resolve_document_fx(doc([(0, 60)]), BOLD, seed=1, layout_has_zoompan=False)
    # A planned draft with no beats (nothing worth an edit) resolves as it is.
    resolved = resolve_document_fx(doc([(0, 60)], fx={"mood": "chill"}), BOLD, seed=1, layout_has_zoompan=False)
    assert resolved.fx.style == BOLD


def test_budgets_are_evaluated_on_the_edit_duration():
    full = resolve_document_fx(doc([(0, 60)], fx=planned()), BOLD, seed=7, layout_has_zoompan=True)
    trimmed = resolve_document_fx(doc([(0, 12), (40, 52)], fx=planned()), BOLD, seed=7, layout_has_zoompan=True)
    # Reframe events (each punch is an "in" with its "out") follow the edit duration, not the source's.
    cap_full = resolver.per10_cap(resolver.REFRAME_RATE["bold"], 60)
    cap_trimmed = resolver.per10_cap(resolver.REFRAME_RATE["bold"], 24)
    punches = lambda d: [c for c in _e_cues(d, "zoom") if c.mode == "punch"]  # noqa: E731
    assert len(punches(full)) <= cap_full and len(punches(trimmed)) <= cap_trimmed < cap_full
    assert len(punches(trimmed)) < len(punches(full))
    assert len(punches(trimmed)) >= 1
    # Every stored cue sits in S inside a played segment.
    for cue in trimmed.fx.cues:
        assert any(a - 1e-6 <= cue.start and cue.end <= b + 1e-6 for a, b in ((0, 12), (40, 52))), cue
    trimmed.validate_duration(60)


def test_reordered_segments_resolve_in_play_order():
    resolved = resolve_document_fx(doc([(30, 45), (0, 15)], fx=planned()), BOLD, seed=3, layout_has_zoompan=True)
    mapped = _e_cues(resolved)
    assert mapped and all(0 <= c.start <= 30 + 1e-6 for c in mapped)
    # The first E second is the quiet head (rule 3): nothing auto starts in [0, 0.25) except hook-bound cues.
    assert not [c for c in mapped if c.start < 0.25 and c.kind not in ("accent", "zoom", "desaturate")]
    resolved.validate_duration(60)


def test_a_repeated_segment_carries_each_cue_once():
    resolved = resolve_document_fx(doc([(0, 15), (0, 15)], fx=planned()), BOLD, seed=5, layout_has_zoompan=True)
    ids = [c.id for c in resolved.fx.cues]
    assert len(ids) == len(set(ids)) and resolved.fx.cues
    assert all(c.end <= 15 + 1e-6 for c in resolved.fx.cues)
    assert all("@" not in c.id for c in resolved.fx.cues)


def test_a_cue_spanning_a_junction_is_split_per_segment(monkeypatch):
    def fake_resolve(inp, style, template, seed):
        cue = {"kind": "desaturate", "id": "b0.desat", "start": 3.0, "end": 7.0, "amount": 0.5, "beat": "b0"}
        return FxPlan.model_validate({"style": style.model_dump(), "template": template, "seed": seed,
                                      "edit_duration": inp.duration, "cues": [cue]})

    monkeypatch.setattr(resolver, "resolve", fake_resolve)
    resolved = resolve_document_fx(doc([(0, 5), (20, 25)], fx=planned()), BOLD, seed=5, layout_has_zoompan=True)
    assert [(c.id, c.start, c.end) for c in resolved.fx.cues] == [("b0.desat", 3.0, 5.0), ("b0.desat~1", 20.0, 22.0)]


def test_user_cues_are_kept_verbatim_and_suppressed_ids_never_return():
    first = resolve_document_fx(doc([(0, 60)], fx=planned()), BOLD, seed=9, layout_has_zoompan=True)
    auto = next(c for c in first.fx.cues if c.kind == "zoom")
    user = {"kind": "sticker", "id": "u0", "start": 2.0, "end": 9.5, "sticker": "text", "text": "look", "origin": "user"}
    fx = {**first.fx.model_dump(), "cues": [*[c.model_dump() for c in first.fx.cues], user], "suppressed": [auto.id]}
    # A cut through the user cue must not change it: it is stored in S and maps itself.
    again = resolve_document_fx(doc([(0, 5), (8, 60)], fx=fx), BOLD, seed=9, layout_has_zoompan=True)
    kept = [c for c in again.fx.cues if c.origin == "user"]
    assert [c.model_dump(mode="json") for c in kept] == [
        {**user, "seed": 0, "beat": ""}
    ]
    assert auto.id not in {c.id for c in again.fx.cues}
    assert again.fx.suppressed == [auto.id]


def test_baseline_pushes_only_without_a_baked_zoom():
    # Slow baseline pushes exist only where the references use them: style-only and subtle (bold/max reframe).
    styled = {"template": "highlighter_swipe"}
    plain = resolve_document_fx(doc([(0, 60)], captions=styled, fx=planned(beats=[])), EditStyle(), seed=2,
                                layout_has_zoompan=False)
    baked = resolve_document_fx(doc([(0, 60)], captions=styled, fx=planned(beats=[])), EditStyle(), seed=2,
                                layout_has_zoompan=True)
    bold = resolve_document_fx(doc([(0, 60)], fx=planned(beats=[])), BOLD, seed=2, layout_has_zoompan=False)
    assert not [c for c in bold.fx.cues if c.id.startswith("kb")]
    kb = lambda d: [c for c in d.fx.cues if c.id.startswith("kb")]  # noqa: E731
    assert kb(plain) and not kb(baked)


def test_style_only_uses_heuristic_beats_without_storing_them():
    styled = doc([(0, 60)], captions={"template": "marker_circle"})
    resolved = resolve_document_fx(styled, EditStyle(), seed=4, layout_has_zoompan=True)
    assert resolved.fx.beats == []
    assert any(c.kind == "accent" for c in resolved.fx.cues)
    assert not any(c.kind in ("zoom", "speed", "freeze") for c in resolved.fx.cues if not c.id.startswith("kb"))
    assert resolved.fx.music is None


def test_turning_off_keeps_beats_and_user_cues_and_drops_the_rest():
    on = resolve_document_fx(doc([(0, 60)], fx=planned()), BOLD, seed=1, layout_has_zoompan=True)
    user = {"kind": "sfx", "id": "u0", "start": 3.0, "end": 3.0, "sound": "pop", "origin": "user"}
    with_user = doc([(0, 60)], fx={**on.fx.model_dump(), "cues": [*on.fx.model_dump()["cues"], user]})
    off = resolve_document_fx(with_user, EditStyle(), seed=1, layout_has_zoompan=True)
    assert [c.id for c in off.fx.cues] == ["u0"]
    assert off.fx.beats == on.fx.beats and off.fx.music is None and off.fx.grade == "none"
    # A legacy draft switched "off" stays exactly as it was (no fx key).
    legacy = doc([(0, 60)])
    assert resolve_document_fx(legacy, EditStyle(), seed=1, layout_has_zoompan=True) is legacy


def test_a_repeated_freeze_never_exceeds_the_hold_budget(monkeypatch):
    def fake_resolve(inp, style, template, seed):
        cue = {"kind": "freeze", "id": "b0.freeze", "start": 4.0, "end": 4.0, "hold": 1.0, "beat": "b0"}
        return FxPlan.model_validate({"style": style.model_dump(), "template": template, "seed": seed,
                                      "edit_duration": inp.duration, "cues": [cue]})

    monkeypatch.setattr(resolver, "resolve", fake_resolve)
    resolved = resolve_document_fx(doc([(0, 30), (0, 30)], fx=planned()), BOLD, seed=5, layout_has_zoompan=True)
    assert mapped_hold_total(resolved) <= fx_hold_budget(60) + 1e-6


# --------------------------------------------------------------------------- render hook (§14.1)


@pytest.fixture
def fake_fx_renderer(monkeypatch):
    calls = []
    module = types.ModuleType("src.editor_fx_render")

    def render_document_fx(document, ctx):
        calls.append((document, ctx))
        return {"level": "full", "applied_warp": []}

    module.render_document_fx = render_document_fx
    monkeypatch.setitem(sys.modules, "src.editor_fx_render", module)
    monkeypatch.setattr(sys.modules["src"], "editor_fx_render", module, raising=False)
    return calls


def _probe(monkeypatch, audio=True):
    monkeypatch.setattr("src.clip_editor._ffprobe_size", lambda _path: (1080, 1920))
    monkeypatch.setattr(editor_document.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout="1\n" if audio else ""))


def test_a_dynamic_edits_draft_renders_through_the_fx_hook(tmp_path, monkeypatch, fake_fx_renderer):
    _probe(monkeypatch)
    monkeypatch.setattr(editor_document, "run_render", lambda *a, **k: pytest.fail("legacy render ran"))
    document = doc([(0, 5), (5, 8), (20, 30)], captions={"template": "slam"}, hookTitle="The secret",
                   effects={"brightness": 120, "blur": 2})
    outcome = editor_document.render_document(tmp_path, "job", document, "reels", None, should_cancel=lambda: False)
    assert outcome == {"level": "full", "applied_warp": []}
    rendered, ctx = fake_fx_renderer[0]
    assert ctx.segments == [("s0", 0, 8), ("s2", 20, 30)]
    assert ctx.input_path == tmp_path / "clean.mp4" and ctx.base_video == "clean"
    assert ctx.output_path == tmp_path / "export-job.mp4"
    assert ctx.head.startswith("[0:v]trim=start=0.0:end=8.0,setpts=PTS-STARTPTS[fxh_v0]")
    assert "concat=n=2:v=1:a=0[fxh_video]" in ctx.head and "lutrgb" in ctx.head and "gblur" in ctx.head
    assert "ass=" not in ctx.head and ctx.head_label == "[fxh_framed]"
    assert ctx.voice_fragment.endswith("concat=n=2:v=0:a=1[fxa_voice0]")
    assert ctx.voice_fragment.startswith("[1:a]atrim=start=0.0:end=8.0")
    assert "-maxrate" in ctx.encode_args and "12M" in ctx.encode_args and "-t" not in ctx.encode_args
    assert ctx.run_render_hooks.should_cancel() is False and (ctx.out_w, ctx.out_h) == (1080, 1920)
    # The ASS builder is the studio's own writer; FX templates leave the words to fx-core.
    monkeypatch.setattr("src.media.captions.build_assemblyai_ass_subtitles",
                        lambda path, *a, **k: fake_fx_renderer.append(k) or Path(path).write_text("ass") or True)
    path = ctx.subtitle_ass_builder(include_captions=False, time_map=lambda t: t * 2, censor_spans=[(1, 2)],
                                    hook_title=False)
    kwargs = fake_fx_renderer[-1]
    assert path.exists() and kwargs["caption_words"] == [] and kwargs["include_captions"] is False
    assert kwargs["hook_title"] is None and kwargs["time_map"](2) == 4 and kwargs["censor_spans"] == [(1, 2)]


def test_a_legacy_draft_never_touches_the_fx_renderer(tmp_path, monkeypatch, fake_fx_renderer):
    _probe(monkeypatch, audio=False)
    commands = []
    monkeypatch.setattr(editor_document, "run_render", lambda command, *a, **k: commands.append(command)
                        or (tmp_path / ".export-job.mp4").write_bytes(b"x"))
    assert editor_document.render_document(tmp_path, "job", doc([(0, 5)]), "tiktok") is None
    assert commands and not fake_fx_renderer


def test_write_ass_defaults_are_the_legacy_call(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr("src.media.captions.build_assemblyai_ass_subtitles",
                        lambda *a, **k: seen.append(k) or True)
    editor_document.write_ass(tmp_path / "a.ass", doc([(0, 5)], hookTitle="Hi"), 1080, 1920)
    assert "time_map" not in seen[0] and "censor_spans" not in seen[0] and seen[0]["hook_title"] == "Hi"
    editor_document.write_ass(tmp_path / "a.ass", doc([(0, 5)], hookTitle="Hi"), 1080, 1920, hook_title="Other")
    assert seen[1]["hook_title"] == "Other"


# --------------------------------------------------------------------------- seeding from the generated clip


def _sidecar(**plan) -> dict:
    fx_plan = FxPlan.model_validate({
        "style": {"intensity": "bold"}, "template": "marker_circle", "seed": 77, "edit_duration": 30, "mood": "funny",
        "cues": [
            {"kind": "accent", "id": "b0.accent", "start": 1.0, "end": 2.0, "variant": "circle", "text": "money"},
            {"kind": "sfx", "id": "b0.sfx", "start": 1.0, "end": 1.0, "sound": "pop"},
            {"kind": "flash", "id": "late", "start": 40.0, "end": 40.2},
        ],
        "grade": "warm", "progress_bar": True, **plan,
    })
    return {
        "style": {"intensity": "bold"},
        "beats": {"words_hash": "h", "beats": [{"id": "b0", "kind": "punchline", "start": 1.0, "end": 2.0}]},
        "plan": fx_plan.model_dump(mode="json"),
        "face_track": {"version": 1, "fps": 6, "samples": [[0.0, [[0.4, 0.2, 0.2, 0.2, 0.9]]]]},
        "fx_level": "full",
        "applied_warp": [],
    }


def test_the_draft_is_seeded_from_the_plan_on_the_clip_timeline():
    seeded = doc_fx_from_sidecar(_sidecar(), 30.0)
    fx = DocFx.model_validate(seeded)
    assert fx.style.intensity == "bold" and fx.seed == 77 and fx.mood == "funny" and fx.grade == "warm"
    assert fx.progressBar and [c.id for c in fx.cues] == ["b0.accent", "b0.sfx"]   # past the clip: dropped
    assert [b.id for b in fx.beats] == ["b0"]
    assert doc_fx_from_sidecar(None, 30) is None and doc_fx_from_sidecar({"plan": None}, 30) is None
    assert doc_fx_from_sidecar({"plan": {"bad": True}}, 30) is None


@pytest.mark.parametrize("missing", ["absent", "null"])
def test_old_drafts_and_the_original_are_upgraded_with_the_plan(missing):
    raw = doc([(0, 30)]).model_dump(mode="json")
    if missing == "null":
        raw["fx"] = None
    upgraded = upgrade_document(raw, {"hook_title": ""}, {}, [], 30.0, _sidecar())
    assert upgraded["fx"]["seed"] == 77
    EditDocument.model_validate(upgraded).validate_duration(60)
    # Without a sidecar plan the draft stays exactly as it was, minus a null key.
    plain = upgrade_document(raw, {"hook_title": ""}, {}, [], 30.0, None)
    assert "fx" not in plain and {k: v for k, v in raw.items() if k != "fx"} == plain


def test_a_draft_whose_edits_were_removed_is_never_reseeded():
    inert = DocFx(style=EditStyle(), disabled=["accent", "sfx"]).model_dump(mode="json")
    raw = {**doc([(0, 30)]).model_dump(mode="json"), "fx": inert}
    assert upgrade_document(raw, {"hook_title": ""}, {}, [], 30.0, _sidecar())["fx"] == inert


def test_generated_emphasis_uses_plan_accents_only_for_fx_templates():
    timed = [{"text": t, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, t in enumerate(TEXT[:12])]
    plan = _sidecar()["plan"]
    heuristic = generated_emphasis(timed, {"caption_template": "hormozi"})
    assert generated_emphasis(timed, {"caption_template": "hormozi"}, plan) == heuristic
    assert generated_emphasis(timed, {"caption_template": "marker_circle"}, plan) == plan_accent_words(timed, plan)
    assert plan_accent_words(timed, plan) == {2, 3}


def test_caption_looks_stay_legacy_unless_asked():
    from src.caption_templates import CAPTION_TEMPLATES
    from src.edit_fx.contracts import FX_CAPTION_TEMPLATES

    legacy = caption_looks()
    assert list(legacy) == list(CAPTION_TEMPLATES) and not any("fx" in look for look in legacy.values())
    flagged = caption_looks(include_fx=True)
    listed = [t for t in FX_CAPTION_TEMPLATES if t in ("highlighter_swipe", "emoji_drop", "behind_head")]
    assert list(flagged) == [*CAPTION_TEMPLATES, *listed]
    assert flagged["emoji_drop"]["fx"] is True and flagged["emoji_drop"]["fxStyle"] == "emoji_drop"
    assert "slam" in caption_looks(include_fx=True, keep=("slam",))   # a stored unlisted style keeps its look
    assert flagged["default"] == legacy["default"]
    kept = caption_looks(keep=("behind_head",))
    assert list(kept) == [*CAPTION_TEMPLATES, "behind_head"]


def test_layout_zoompan_detection():
    assert layout_filter_has_zoompan({"vertical_plan": ("crop=1:1,zoompan=z=1.05", "single")})
    assert not layout_filter_has_zoompan({"vertical_plan": ("crop=1:1", "single")})
    assert not layout_filter_has_zoompan({"kind": "fit"}) and not layout_filter_has_zoompan(None)


async def test_prepare_seeds_dynamic_edits_face_track_and_zoom_report(tmp_path, monkeypatch, editor_store):
    from src.config import Config
    from src.editor_state import editor_key
    from src.media_storage import MediaStorage

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    key = editor_key("task", "clip", "clip.mp4")
    assert await editor_store.claim_prepare(key, "task", "clip", "clip.mp4", "job") == "claimed"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    clip = {"id": "clip", "task_id": "task", "filename": "clip.mp4", "file_path": str(tmp_path / "clip.mp4"),
            "text": ""}
    task = {"id": "task", "user_id": "owner", "source_url": "upload://source.mp4", "source_type": "video_url",
            "caption_template": "marker_circle"}
    service = SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        cache_repo=SimpleNamespace(get_cache=AsyncMock(return_value={"video_path": str(source)})),
        config=SimpleNamespace(default_processing_mode="fast"),
        video_service=SimpleNamespace(prepare_source=AsyncMock(side_effect=lambda path: path)),
        _get_clip_source_ranges=lambda _clip: [[0, 30]],
        _media_limits=AsyncMock(return_value=(None, None)),
        _build_cache_key=lambda *args: "key",
        _load_task_source_settings=AsyncMock(return_value={"output_format": "vertical"}),
        _cleanup_source_video=lambda *args: None,
    )

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(close=AsyncMock())

    def bake(clean, output_format, src, ranges, preserve, stored, report=None):
        (clean.parent / "layout.mp4").write_bytes(b"layout")
        (clean.parent / "layout.json").write_text(json.dumps({"landscapeRecommended": False}))
        report["zoompan"] = True
        return True

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _: service)
    monkeypatch.setattr("src.youtube_source.local_ranges_for", lambda _source, ranges, **_kwargs: ranges)
    monkeypatch.setattr("src.video_utils.create_optimized_clip", lambda *a, **k: a[3].write_bytes(b"clean") or True)
    monkeypatch.setattr("src.video_utils.load_cached_transcript_data", lambda _: None)
    monkeypatch.setattr("src.clip_source_map.load_clip_edit_fx", lambda _path: _sidecar())
    monkeypatch.setattr(editor_service, "bake_editor_layout", bake)
    monkeypatch.setattr(editor_service, "make_assets", lambda _: {
        "status": "ready", "width": 1080, "height": 1920, "fps": 30, "duration": 30.0, "hasAudio": True,
        "waveform": [],
    })
    monkeypatch.setattr(MediaStorage, "save_source_metadata", lambda *a, **k: None)
    monkeypatch.setattr(MediaStorage, "publish_editor_media", lambda *a, **k: None)
    await editor_service.prepare_editor({}, "task", "clip", "clip.mp4")
    state = await stored_state("task", "clip", "clip.mp4")
    assert state["status"] == "ready", state
    assert state["layoutHasZoompan"] is True and state["hasLayout"] is True
    assert state["faceTrack"]["base"] == "layout" and state["faceTrack"]["track"]["fps"] == 6
    draft = state["draft"]["document"]
    assert draft["fx"]["seed"] == 77 and draft["framing"]["fit"] == "auto"
    assert state["original"]["fx"] == draft["fx"]


async def test_export_binds_the_session_fx_inputs_around_a_dynamic_edits_render(tmp_path, monkeypatch, editor_store):
    from src.config import Config
    from src.editor_document import editor_dir

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    bound, rendered = [], []
    module = types.ModuleType("src.editor_fx_render")

    @asynccontextmanager
    async def studio_fx_inputs(document, asset, directory, *, storage=None):
        bound.append(asset)
        yield None

    module.studio_fx_inputs = studio_fx_inputs
    monkeypatch.setitem(sys.modules, "src.editor_fx_render", module)
    monkeypatch.setattr(sys.modules["src"], "editor_fx_render", module, raising=False)

    def render(directory, job_id, document, preset, broll_files, **kwargs):
        rendered.append((bool(bound), document.fx is not None))
        (directory / f"export-{job_id}.mp4").write_bytes(b"x")

    @asynccontextmanager
    async def sessions():
        yield object()

    service = SimpleNamespace(clip_repo=SimpleNamespace(
        get_clip_by_id=AsyncMock(return_value={"id": "clip", "task_id": "task"})))
    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _db: service)
    monkeypatch.setattr(editor_service, "cap_export_for_plan", AsyncMock())
    monkeypatch.setattr(editor_service, "restore_session_media", AsyncMock())
    monkeypatch.setattr(editor_service, "render_document", render)
    asset = {"duration": 60, "faceTrack": {"base": "clean", "track": {}}, "matteVersion": "m1"}
    for fx_doc, expect_bound in ((doc([(0, 10)], captions={"template": "slam"}), True), (doc([(0, 10)]), False)):
        bound.clear()
        key = editor_store.put_ready("task", "clip", "clip.mp4", asset, fx_doc.model_dump())
        editor_dir(str(tmp_path), "task", "clip", "clip.mp4").mkdir(parents=True, exist_ok=True)
        job = (("e" if expect_bound else "f") * 32)
        assert await editor_store.create_job(key, job, "export", {"document": fx_doc.model_dump(), "preset": "tiktok"})
        await editor_service.export_editor({}, "task", "clip", "clip.mp4", job)
        assert (await editor_store.job(key, job))["status"] == "completed"
        assert bound == ([{"status": "ready", **asset}] if expect_bound else [])
    assert rendered == [(True, False), (False, False)]
