"""edit_fx.compat.known_fields: forward-lenient reads of persisted data (AI-M9)."""

import logging

from src.edit_fx.compat import known_fields, log_dropped
from src.edit_fx.contracts import EditBeats, EditStyle, FaceTrack, FxPlan, FxScene


def plan_payload(**extra):
    return {"style": {"intensity": "bold"}, "template": "slam", "seed": 1, "edit_duration": 8.0, **extra}


def test_known_payload_is_unchanged():
    raw = plan_payload(cues=[{"kind": "sfx", "id": "a", "start": 1, "end": 1, "sound": "pop"}],
                       music={"mood": "hype", "bed": "hype"})
    clean, dropped = known_fields(FxPlan, raw)
    assert clean == raw and dropped == []


def test_drops_unknown_keys_at_every_depth_and_reports_paths():
    raw = plan_payload(
        future_global=1,
        style={"intensity": "bold", "laser_eyes": True},
        music={"mood": "hype", "bed": "hype", "stems": 4},
        cues=[
            {"kind": "zoom", "id": "z", "start": 0, "end": 1, "mode": "punch", "scale": 1.2, "easing": "spring"},
            {"kind": "sfx", "id": "s", "start": 1, "end": 1, "sound": "pop"},
        ],
    )
    clean, dropped = known_fields(FxPlan, raw)
    assert sorted(dropped) == ["cues[0].easing", "future_global", "music.stems", "style.laser_eyes"]
    plan = FxPlan.model_validate(clean)
    assert plan.style.intensity == "bold" and len(plan.cues) == 2


def test_unknown_cue_kind_drops_only_that_cue():
    raw = plan_payload(cues=[
        {"kind": "sfx", "id": "s0", "start": 1, "end": 1, "sound": "pop"},
        {"kind": "hologram", "id": "h", "start": 1, "end": 2, "depth": 3},
        {"id": "nokind", "start": 1, "end": 2},
        {"kind": "flash", "id": "f", "start": 2, "end": 2.1},
    ])
    clean, dropped = known_fields(FxPlan, raw)
    assert dropped == ["cues[1]", "cues[2]"]
    assert [cue.id for cue in FxPlan.model_validate(clean).cues] == ["s0", "f"]


def test_nested_lists_of_models_and_optionals():
    raw = {
        "words_hash": "x", "beats": [{"id": "b0", "kind": "hook", "start": 0, "end": 1, "mood_score": 2}],
        "reactions": [{"at": 1, "query": "wow", "gif_rating": "g"}], "speaker": "A",
    }
    clean, dropped = known_fields(EditBeats, raw)
    assert sorted(dropped) == ["beats[0].mood_score", "reactions[0].gif_rating", "speaker"]
    EditBeats.model_validate(clean)


def test_scene_optional_models_and_fixed_tuples():
    raw = {
        "width": 1080, "height": 1920, "edit_duration": 4, "out_duration": 4, "template": "slam",
        "caption": {"template": "slam", "font": "F", "size_px": 60, "color": "#FFFFFF", "accent": "#FFE000",
                    "y": 0.7, "outline_glow": 2, "placements": [{"key": "k", "x": 0, "y": 0, "rot": 9}]},
        "face_track": {"samples": [[0.0, [[0.1, 0.2, 0.3, 0.4, 0.9]]]], "model": "yunet"},
        "hook_window": [0.12, 3.0], "broll_windows": [[1, 2]],
        "matte_windows": [{"start": 1, "end": 2, "person": True, "confidence": 1}],
    }
    clean, dropped = known_fields(FxScene, raw)
    assert sorted(dropped) == ["caption.outline_glow", "caption.placements[0].rot", "face_track.model",
                               "matte_windows[0].confidence"]
    assert clean["hook_window"] == [0.12, 3.0] and clean["face_track"]["samples"] == raw["face_track"]["samples"]
    FxScene.model_validate(clean)


def test_none_values_and_wrong_types_pass_through_for_the_validator():
    clean, dropped = known_fields(FxScene, {"caption": None, "cues": "nope", "face_track": 3})
    assert clean == {"caption": None, "cues": "nope", "face_track": 3} and dropped == []


def test_input_is_not_mutated():
    raw = {"intensity": "bold", "x": 1}
    known_fields(EditStyle, raw)
    assert raw == {"intensity": "bold", "x": 1}


def test_non_dict_input():
    assert known_fields(EditStyle, None) == ({}, [])
    assert known_fields(FaceTrack, ["x"]) == ({}, [])


def test_log_dropped(caplog):
    with caplog.at_level(logging.INFO, logger="src.edit_fx.compat"):
        log_dropped("edit_style", [])
        log_dropped("edit_style", ["a", "b.c"])
    assert [r.getMessage() for r in caplog.records] == ["fx_compat_dropped_keys where=edit_style keys=a,b.c"]
