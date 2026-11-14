"""Dynamic-edit sidecar keys in clip_source_map (§3.3): written only on the FX path, read leniently, never deleted
by a failed read."""

import json
import logging

import pytest
from pydantic import TypeAdapter

from src.clip_source_map import (
    FX_SIDECAR_KEYS,
    clear_clip_edit_fx,
    clip_source_map_path,
    load_clip_broll,
    load_clip_edit_fx,
    load_clip_source_ranges,
    save_clip_broll,
    save_clip_caption_settings,
    save_clip_edit_fx,
    save_clip_source_ranges,
    save_clip_vertical_layout,
)
from src.edit_fx.contracts import EditBeats, EditStyle, FxCue, FxPlan
from src.edit_fx.warp import build_warp

PLAN = FxPlan(
    style=EditStyle(intensity="bold"), template="slam", seed=7, edit_duration=6.0,
    cues=[{"kind": "freeze", "id": "b1.freeze", "start": 2.0, "end": 2.0, "hold": 0.5, "label": "WAIT"},
          {"kind": "sfx", "id": "b1.sfx", "start": 2.0, "end": 2.0, "sound": "impact"}],
).model_dump(mode="json")
BEATS = EditBeats(words_hash="abc", beats=[{"id": "b1", "kind": "punchline", "start": 2.0, "end": 2.4,
                                            "emoji": "🤯"}]).model_dump(mode="json")
WARP = build_warp([TypeAdapter(FxCue).validate_python(c) for c in PLAN["cues"]], 6.0).to_json()
TRACK = {"version": 1, "space": "picture", "fps": 30,
         "samples": [[i / 30, [[0.412345, 0.3, 0.2, 0.2, 0.987654]]] for i in range(60)]}


def legacy_clip(tmp_path):
    clip = tmp_path / "clip.mp4"
    save_clip_source_ranges(clip, [(10.0, 16.0)])
    save_clip_caption_settings(clip, {"font_size": 30})
    save_clip_broll(clip, [{"start": 1.0, "duration": 2.0, "footage": {"id": 1}}])
    return clip


def payload(clip):
    return json.loads(clip_source_map_path(clip).read_text(encoding="utf-8"))


def save(clip, **overrides):
    kwargs = dict(style={"intensity": "bold"}, beats=BEATS, plan=PLAN, face_track=TRACK, fx_level="full",
                  applied_warp=WARP)
    save_clip_edit_fx(clip, **{**kwargs, **overrides})


def test_legacy_sidecar_has_no_fx_keys(tmp_path):
    clip = legacy_clip(tmp_path)
    assert not set(FX_SIDECAR_KEYS) & set(payload(clip))
    assert load_clip_edit_fx(clip) is None


def test_clear_never_creates_or_rewrites(tmp_path):
    missing = tmp_path / "missing.mp4"
    clear_clip_edit_fx(missing)
    assert not clip_source_map_path(missing).exists()
    clip = legacy_clip(tmp_path)
    before = clip_source_map_path(clip).read_bytes()
    clear_clip_edit_fx(clip)
    assert clip_source_map_path(clip).read_bytes() == before


def test_save_and_load_round_trip_keeping_other_keys(tmp_path):
    clip = legacy_clip(tmp_path)
    save(clip)
    data = payload(clip)
    assert set(FX_SIDECAR_KEYS) <= set(data)
    assert load_clip_source_ranges(clip) == [(10.0, 16.0)] and load_clip_broll(clip)
    assert data["caption_settings"] == {"font_size": 30}

    loaded = load_clip_edit_fx(clip)
    assert loaded["style"] == {"intensity": "bold"}
    assert loaded["plan"] == PLAN and loaded["beats"] == BEATS
    assert loaded["fx_level"] == "full" and loaded["applied_warp"] == WARP
    assert loaded["face_track"]["fps"] == 6 and len(loaded["face_track"]["samples"]) == 12   # decimated
    assert loaded["face_track"]["samples"][1][1][0] == [0.412, 0.3, 0.2, 0.2, 0.988]          # 3 decimals


def test_style_only_and_optional_fields(tmp_path):
    clip = tmp_path / "clip.mp4"
    save(clip, style={}, beats=None, face_track=None, fx_level="safe", applied_warp=[])
    loaded = load_clip_edit_fx(clip)
    assert loaded["style"] == {} and loaded["beats"] is None and loaded["face_track"] is None
    assert loaded["applied_warp"] == [] and loaded["fx_level"] == "safe"
    assert "edit_beats" not in payload(clip) and "face_track" not in payload(clip)


def test_resave_drops_stale_beats_and_track(tmp_path):
    clip = tmp_path / "clip.mp4"
    save(clip)
    save(clip, beats=None, face_track=None)
    assert "edit_beats" not in payload(clip) and "face_track" not in payload(clip)


def test_bad_level_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        save(tmp_path / "clip.mp4", fx_level="extreme")


def test_clear_removes_only_fx_keys(tmp_path):
    clip = legacy_clip(tmp_path)
    save_clip_vertical_layout(clip, {"kind": "crop", "video_filter": "crop=1:1", "mode": "vf", "width": 1,
                                     "height": 1, "duration": 6})
    save(clip)
    clear_clip_edit_fx(clip)
    data = payload(clip)
    assert not set(FX_SIDECAR_KEYS) & set(data)
    assert {"source_ranges", "caption_settings", "broll", "vertical_layout"} <= set(data)


def test_newer_keys_are_dropped_not_fatal(tmp_path, caplog):
    clip = tmp_path / "clip.mp4"
    save(clip)
    data = payload(clip)
    data["edit_style"]["laser_eyes"] = True
    data["edit_fx"]["cues"].append({"kind": "hologram", "id": "h", "start": 1, "end": 2})
    data["edit_fx"]["future"] = {"x": 1}
    data["edit_fx_warp"][0]["curve"] = "ease"
    clip_source_map_path(clip).write_text(json.dumps(data), encoding="utf-8")
    with caplog.at_level(logging.INFO):
        loaded = load_clip_edit_fx(clip)
    assert loaded["style"] == {"intensity": "bold"}
    assert len(loaded["plan"]["cues"]) == 2 and loaded["applied_warp"] == WARP
    assert any("fx_compat_dropped_keys" in r.getMessage() for r in caplog.records)


def test_corrupt_field_is_none_for_that_field_only_and_never_deleted(tmp_path, caplog):
    clip = tmp_path / "clip.mp4"
    save(clip)
    data = payload(clip)
    data["edit_beats"] = {"words_hash": "x", "beats": [{"id": "b0", "kind": "hook", "start": 5, "end": 1}]}
    data["face_track"] = "garbage"
    data["fx_level"] = "turbo"
    data["edit_fx_warp"] = [{"e0": 0, "e1": 1}]
    clip_source_map_path(clip).write_text(json.dumps(data), encoding="utf-8")
    before = clip_source_map_path(clip).read_bytes()
    with caplog.at_level(logging.WARNING):
        loaded = load_clip_edit_fx(clip)
    assert loaded["beats"] is None and loaded["face_track"] is None and loaded["fx_level"] is None
    assert loaded["applied_warp"] == [] and loaded["plan"] == PLAN
    fields = {r.getMessage().split("field=")[1].split()[0] for r in caplog.records if "fx_sidecar_corrupt" in r.getMessage()}
    assert fields == {"edit_beats", "face_track", "fx_level", "edit_fx_warp"}
    assert clip_source_map_path(clip).read_bytes() == before


def test_corrupt_plan_still_reports_an_fx_clip(tmp_path):
    clip = tmp_path / "clip.mp4"
    save(clip)
    data = payload(clip)
    data["edit_fx"] = {"template": 5}
    data["edit_style"] = "bold"
    clip_source_map_path(clip).write_text(json.dumps(data), encoding="utf-8")
    loaded = load_clip_edit_fx(clip)
    assert loaded is not None and loaded["plan"] is None and loaded["style"] == {}


def test_unreadable_sidecar_file(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip_source_map_path(clip).write_text("{not json", encoding="utf-8")
    assert load_clip_edit_fx(clip) is None


def test_source_range_rewrite_keeps_fx_keys(tmp_path):
    clip = tmp_path / "clip.mp4"
    save_clip_source_ranges(clip, [(0.0, 6.0)])
    save(clip)
    save_clip_source_ranges(clip, [(1.0, 7.0)])
    assert load_clip_edit_fx(clip)["plan"] == PLAN


def test_a_cue_with_an_unknown_value_costs_that_cue_not_the_plan(tmp_path, caplog):
    clip = tmp_path / "clip.mp4"
    save(clip)
    data = payload(clip)
    data["edit_fx"]["cues"].append({"kind": "accent", "id": "b2.accent", "start": 3.0, "end": 3.5,
                                    "variant": "glitter_v2", "text": "wow"})
    clip_source_map_path(clip).write_text(json.dumps(data), encoding="utf-8")
    before = clip_source_map_path(clip).read_bytes()
    with caplog.at_level(logging.WARNING):
        loaded = load_clip_edit_fx(clip)
    assert loaded["plan"] == PLAN
    assert any("fx_sidecar_corrupt" in r.getMessage() and "edit_fx.cues" in r.getMessage() for r in caplog.records)
    assert clip_source_map_path(clip).read_bytes() == before


@pytest.mark.parametrize("fps, expected_fps, kept", [(30, 6.0, 12), (15, 5.0, 20), (12, 6.0, 30), (6, 6.0, 60)])
def test_face_track_is_decimated_to_at_most_6_fps(tmp_path, fps, expected_fps, kept):
    clip = tmp_path / "clip.mp4"
    track = {**TRACK, "fps": fps, "samples": [[i / fps, [[0.4, 0.3, 0.2, 0.2, 0.9]]] for i in range(60)]}
    save(clip, face_track=track)
    stored = payload(clip)["face_track"]
    assert stored["fps"] == expected_fps and stored["fps"] <= 6 and len(stored["samples"]) == kept


def test_malformed_face_track_is_dropped_not_raised(tmp_path):
    clip = tmp_path / "clip.mp4"
    save(clip, face_track={"fps": 6, "samples": [[0.0, [["x"]]]]})
    assert "face_track" not in payload(clip) and payload(clip)["fx_level"] == "full"
