"""edit_fx.contracts: the persisted shapes (§3.1)."""

import json
import typing

import pytest
from pydantic import TypeAdapter, ValidationError

from src.edit_fx import contracts as c

CUE = TypeAdapter(c.FxCue)

EVERY_CUE = [
    {"kind": "accent", "id": "b0.accent", "start": 0.5, "end": 1.4, "variant": "counter", "text": "$2.5M",
     "value": 2_500_000, "prefix": "$", "suffix": "M", "seed": 7, "beat": "b0"},
    {"kind": "zoom", "id": "b0.punch", "start": 0.5, "end": 1.2, "mode": "punch", "scale": 1.15},
    {"kind": "shake", "id": "b1.shake", "start": 2.0, "end": 2.3, "amp": 14},
    {"kind": "flash", "id": "b1.flash", "start": 2.0, "end": 2.1, "color": "#FFAA00"},
    {"kind": "glitch", "id": "b2.glitch", "start": 3.0, "end": 3.2, "amount": 0.7},
    {"kind": "desaturate", "id": "b2.desat", "start": 3.0, "end": 4.0, "amount": 0.9, "dim": 0.2},
    {"kind": "transition", "id": "g.t0", "start": 5.0, "end": 5.0, "style": "whip"},
    {"kind": "sfx", "id": "b1.sfx", "start": 2.0, "end": 2.0, "sound": "impact", "variant": 2, "gain_db": -3},
    {"kind": "bleep", "id": "b3.bleep", "start": 6.0, "end": 6.4, "cover": "pixelate"},
    {"kind": "speed", "id": "b4.speed", "start": 7.0, "end": 8.0, "rate": 1.3},
    {"kind": "freeze", "id": "b5.freeze", "start": 9.0, "end": 9.0, "hold": 0.8, "label": "WAIT"},
    {"kind": "sticker", "id": "g.wait", "start": 1.0, "end": 2.5, "sticker": "wait_for_it"},
    {"kind": "reaction", "id": "r0", "start": 4.0, "end": 5.5, "query": "mind blown", "asset_id": "abc",
     "url": "https://media.giphy.com/media/abc/giphy.mp4"},
]


def plan(**extra):
    return c.FxPlan(style=c.EditStyle(intensity="bold"), template="marker_circle", seed=123, edit_duration=12.0,
                    **extra)


def test_every_cue_kind_is_listed():
    assert {cue["kind"] for cue in EVERY_CUE} == set(c.FX_KINDS)
    assert set(typing.get_args(c.FxKind)) == set(c.FX_KINDS)


def test_fx_plan_round_trips_with_every_cue_kind():
    original = plan(cues=EVERY_CUE, music={"mood": "hype", "bed": "hype"}, grade="teal_orange", grain=0.3,
                    progress_bar=True, loop_ending="crossfade", hook_title_suppressed=True, mood="hype")
    dumped = original.model_dump(mode="json")
    restored = c.FxPlan.model_validate(json.loads(json.dumps(dumped)))
    assert restored == original
    assert [cue.kind for cue in restored.cues] == [cue["kind"] for cue in EVERY_CUE]


def test_edit_style_to_json_is_empty_when_off():
    assert c.EditStyle().to_json() == {}
    assert c.EditStyle(intensity="off", sfx=False, grade="noir").to_json() == {}


def test_edit_style_to_json_keeps_intensity_and_non_defaults_sorted():
    style = c.EditStyle(intensity="bold", progress_bar=True, music_mood="hype", sfx=True)
    assert style.to_json() == {"intensity": "bold", "music_mood": "hype", "progress_bar": True}
    assert list(style.to_json()) == sorted(style.to_json())
    assert c.EditStyle(intensity="subtle").to_json() == {"intensity": "subtle"}


def test_fingerprint_follows_to_json():
    assert c.EditStyle(intensity="bold").fingerprint() == c.EditStyle(intensity="bold", sfx=True).fingerprint()
    assert c.EditStyle(intensity="bold").fingerprint() != c.EditStyle(intensity="max").fingerprint()
    assert len(c.EditStyle().fingerprint()) == 16


def test_models_are_strict_and_frozen():
    with pytest.raises(ValidationError):
        c.EditStyle(intensity="bold", turbo=True)
    with pytest.raises(ValidationError):
        c.EditStyle(intensity="extreme")
    style = c.EditStyle()
    with pytest.raises(ValidationError):
        style.sfx = False
    with pytest.raises(ValidationError):
        c.Beat(id="b0", kind="hook", start=0, end=1, confidence=0.5)


def test_instant_cues_must_have_no_length():
    for kind, extra in (("sfx", {"sound": "pop"}), ("freeze", {"hold": 0.5}), ("transition", {"style": "flash"})):
        with pytest.raises(ValidationError):
            CUE.validate_python({"kind": kind, "id": "x", "start": 1.0, "end": 1.5, **extra})


def test_spans_cannot_end_before_they_start():
    with pytest.raises(ValidationError):
        CUE.validate_python({"kind": "flash", "id": "x", "start": 2.0, "end": 1.0})
    with pytest.raises(ValidationError):
        c.Beat(id="b0", kind="hook", start=2, end=1)


def test_unknown_cue_kind_is_rejected_by_the_strict_contract():
    with pytest.raises(ValidationError):
        CUE.validate_python({"kind": "laser", "id": "x", "start": 0, "end": 1})


def test_numbers_and_colours_are_bounded():
    with pytest.raises(ValidationError):
        CUE.validate_python({"kind": "zoom", "id": "x", "start": 0, "end": 1, "mode": "punch", "scale": 2.0})
    with pytest.raises(ValidationError):
        CUE.validate_python({"kind": "flash", "id": "x", "start": 0, "end": 1, "color": "white"})
    with pytest.raises(ValidationError):
        CUE.validate_python({"kind": "flash", "id": "x", "start": 0, "end": float("inf")})


def test_plan_properties():
    assert plan().draws                                   # FX template draws captions
    sfx_only = c.FxPlan(style=c.EditStyle(intensity="subtle"), template="default", seed=1, edit_duration=5,
                        cues=[EVERY_CUE[7], {**EVERY_CUE[8], "cover": "none"}])
    assert not sfx_only.draws and sfx_only.has_audio and not sfx_only.needs_face and not sfx_only.has_warp
    covered = sfx_only.model_copy(update={"cues": (CUE.validate_python(EVERY_CUE[8]),)})
    assert covered.draws and covered.needs_face
    full = plan(cues=EVERY_CUE)
    assert full.has_warp and full.has_audio and full.needs_face and not full.needs_matte
    behind = plan(cues=[{"kind": "accent", "id": "a", "start": 0, "end": 1, "variant": "behind"}])
    assert behind.needs_matte
    music = c.FxPlan(style=c.EditStyle(intensity="subtle"), template="default", seed=1, edit_duration=5,
                     music={"mood": "chill", "bed": "chill"})
    assert music.has_audio and not music.draws
    assert c.FxPlan(style=c.EditStyle(), template="default", seed=1, edit_duration=5, grain=0.2).draws


def test_template_lists():
    assert len(c.FX_CAPTION_TEMPLATES) == 9 and len(c.LEGACY_CAPTION_TEMPLATES) == 7
    assert not set(c.FX_CAPTION_TEMPLATES) & set(c.LEGACY_CAPTION_TEMPLATES)
    assert c.is_fx_template("slam") and not c.is_fx_template("default") and not c.is_fx_template(None)


def test_literal_tuples_match_their_types():
    assert c.INTENSITIES == typing.get_args(c.Intensity)
    assert c.MOODS == typing.get_args(c.Mood)
    assert ("auto", *c.MOODS) == typing.get_args(c.EditStyle.model_fields["music_mood"].annotation)
    assert "aggressive" in c.MOODS
    assert set(typing.get_args(c.EditPlanLLM.model_fields["mood"].annotation)) == set(c.MOODS)
    assert c.GRADES == typing.get_args(c.GradeId)
    assert c.SOUND_IDS == typing.get_args(c.SoundId)
    assert set(c.TRANSITION_HALF) == set(typing.get_args(c.TransitionStyle))
    assert c.INSTANT_KINDS <= set(c.FX_KINDS)


def test_llm_schema_has_only_required_fields():
    def walk(schema, defs):
        if "$ref" in schema:
            schema = defs[schema["$ref"].split("/")[-1]]
        if schema.get("type") == "object" and "properties" in schema:
            assert set(schema.get("required", [])) == set(schema["properties"]), schema.get("title")
            for prop in schema["properties"].values():
                walk(prop, defs)
        if "items" in schema:
            walk(schema["items"], defs)

    schema = c.EditPlanLLM.model_json_schema()
    walk(schema, schema.get("$defs", {}))


def test_scene_round_trip():
    scene = c.FxScene(
        width=1080, height=1920, edit_duration=10, out_duration=10.5, template="slam",
        warp=[{"e0": 0, "e1": 5, "o0": 0, "rate": 1}, {"e0": 5, "e1": 5, "o0": 5, "rate": 0, "hold": 0.5},
              {"e0": 5, "e1": 10, "o0": 5.5, "rate": 1}],
        cues=EVERY_CUE[:3], words=[{"id": "w0", "start": 0, "end": 0.4, "text": "hey"}],
        caption={"template": "slam", "font": "THEBOLDFONT", "size_px": 70, "color": "#FFFFFF", "accent": "#FFE000",
                 "y": 0.78, "placements": [{"key": "[\"s0\",\"w0\"]", "x": 0.1, "y": 0}]},
        hook_window=(0.12, 4.0), face_track={"samples": [[0.0, [[0.4, 0.3, 0.2, 0.15, 0.9]]]]},
        broll_windows=[(1.0, 2.0)], matte_windows=[{"start": 1, "end": 2, "person": True}],
    )
    assert c.FxScene.model_validate(json.loads(scene.model_dump_json())) == scene
