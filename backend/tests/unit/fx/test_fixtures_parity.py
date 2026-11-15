"""The cross-language fixtures (G2) in shared/fx/fixtures: Python reproduces every case, and the generator is
pinned (it refuses to overwrite without --accept). vitest checks the TypeScript mirror against the same files."""

import importlib.util
import json
import math
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from src.edit_fx import constants
from src.edit_fx.caption_groups import grouped_words, phrase_groups
from src.edit_fx.censor import censor_text, censored_words
from src.edit_fx.caption_rows import group_rows, mapped_caption_rows
from src.edit_fx.contracts import CaptionRow, FxCue
from src.edit_fx.duck import duck_envelope, envelope_db_at, speech_spans
from src.edit_fx.mapping import mapped_cues
from src.edit_fx.rng import fnv1a32, hash01, mulberry32
from src.edit_fx.warp import build_warp

BACKEND = Path(__file__).resolve().parents[3]
SHARED = BACKEND.parent / "shared" / "fx"
FIXTURES = SHARED / "fixtures"
FAMILIES = ("rng", "warp", "mapping", "duck", "censor", "geometry", "groups", "rows")
CUE = TypeAdapter(FxCue)


def load_generator():
    spec = importlib.util.spec_from_file_location("fx_gen_fixtures", BACKEND / "scripts" / "fx" / "gen_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def all_cases():
    # Only the shared families: shared/fx/fixtures/scene (studio scenes) is checked by test_editor_fx_render.py.
    return sorted(path for family in FAMILIES for path in (FIXTURES / family).glob("*.json"))


def close(actual, expected, path="$"):
    """Deep equality with 1e-6 float tolerance (ints and bools exact)."""
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        assert actual == expected, path
    elif isinstance(expected, (int, float)):
        assert isinstance(actual, (int, float)) and not isinstance(actual, bool), path
        assert math.isclose(actual, expected, rel_tol=0, abs_tol=1e-6), f"{path}: {actual} != {expected}"
    elif isinstance(expected, list):
        assert isinstance(actual, (list, tuple)) and len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected)):
            close(a, e, f"{path}[{i}]")
    elif isinstance(expected, dict):
        assert isinstance(actual, dict) and set(actual) == set(expected), path
        for key in expected:
            close(actual[key], expected[key], f"{path}.{key}")
    else:
        raise AssertionError(f"{path}: unexpected {type(expected)}")


def compute(fixture):
    fn, inp = fixture["fn"], fixture["input"]
    if fn == "fnv1a32":
        return fnv1a32(inp["text"])
    if fn == "mulberry32":
        draw = mulberry32(inp["seed"])
        return [draw() for _ in range(inp["n"])]
    if fn == "hash01":
        return hash01(inp["seed"], inp["n"])
    if fn == "build_warp":
        warp = build_warp([CUE.validate_python(c) for c in inp["cues"]], inp["edit_duration"])
        return {
            "pieces": [{"e0": p.e0, "e1": p.e1, "o0": p.o0, "rate": p.rate, "hold": p.hold} for p in warp.pieces],
            "edit_duration": warp.edit_duration, "out_duration": warp.out_duration, "nontrivial": warp.nontrivial,
            "to_output": [warp.to_output(e) for e in inp["probes_e"]],
            "to_edit": [warp.to_edit(o) for o in inp["probes_o"]],
            "json": warp.to_json(),
        }
    if fn == "mapped_cues":
        out = mapped_cues([tuple(s) for s in inp["segments"]], [CUE.validate_python(c) for c in inp["cues"]],
                          min_piece=inp["min_piece"])
        return [CUE.dump_python(c, mode="json") for c in out]
    if fn == "duck_envelope":
        spans = speech_spans([tuple(w) for w in inp["words_final"]], inp["merge_gap"])
        points = duck_envelope(spans, **inp["params"])
        return {"spans": [list(s) for s in spans], "points": [list(p) for p in points],
                "at": [envelope_db_at(points, t) for t in inp["probes"]]}
    if fn == "censor_text":
        return censor_text(inp["text"])
    if fn == "censored_words":
        return censored_words(inp["words"], [tuple(s) for s in inp["spans"]])
    if fn == "phrase_groups":
        order = grouped_words(inp["words"])
        return [{"first": g.first, "last": g.last, "start": g.start, "end": g.end,
                 "text": " ".join(str(w["text"]) for w in order[g.first:g.last + 1])}
                for g in phrase_groups(inp["words"], inp["max_words"])]
    if fn == "group_rows":
        return group_rows(inp["words"], inp["max_words"], [CaptionRow.model_validate(r) for r in inp["rows"]], inp["y"])
    if fn == "mapped_caption_rows":
        rows = mapped_caption_rows([tuple(s) for s in inp["segments"]],
                                   [CaptionRow.model_validate(r) for r in inp["rows"]])
        return [r.model_dump(mode="json") for r in rows]
    if fn == "geometry":
        x, y, w, h = constants.reaction_rect(inp["width"], inp["height"])
        return {"unit": constants.unit(inp["width"], inp["height"]),
                "reaction_rect": {"x": x, "y": y, "w": w, "h": h},
                "caption_safe": constants.caption_safe(inp["width"], inp["height"]), "hook_band": constants.HOOK_BAND}
    raise AssertionError(f"unknown fixture fn {fn}")


def test_every_family_has_at_least_12_cases():
    for family in FAMILIES:
        assert len(list((FIXTURES / family).glob("*.json"))) >= 12, family


@pytest.mark.parametrize("path", all_cases(), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_python_reproduces_fixture(path):
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert fixture["family"] == path.parent.name and fixture["case"] == path.stem
    actual = compute(fixture)
    if fixture["family"] == "rng":
        assert actual == fixture["expected"]                  # bit-exact
    else:
        close(json.loads(json.dumps(actual)), fixture["expected"])


def test_required_edge_cases_are_covered():
    names = {p.stem for p in all_cases()}
    for name in ("freeze_at_zero", "freeze_at_end", "empty_plan", "overlapping_speed_earlier_wins",
                 "repeated_segment", "reordered_segments", "instant_at_segment_end_noncontiguous",
                 "size_1080x1920", "size_1080x1080", "size_1920x1080"):
        assert name in names


def test_committed_files_match_the_generator():
    outputs = load_generator().build_outputs()
    for rel, content in outputs.items():
        assert (SHARED / rel).read_text(encoding="utf-8") == content, f"{rel} is stale: run gen_fixtures.py --accept"


def test_schemas_validate_their_own_samples():
    cue_schema = json.loads((SHARED / "fx_cue.schema.json").read_text(encoding="utf-8"))
    scene_schema = json.loads((SHARED / "fx_scene.schema.json").read_text(encoding="utf-8"))
    kinds = {variant["$ref"].split("/")[-1] for variant in cue_schema["oneOf"]}
    assert len(kinds) == 13 and cue_schema["discriminator"]["propertyName"] == "kind"
    assert {"width", "height", "edit_duration", "out_duration", "template"} <= set(scene_schema["required"])


def test_generator_refuses_to_overwrite_without_accept(tmp_path, capsys):
    generator = load_generator()
    assert generator.main(["--out", str(tmp_path)]) == 0                   # fresh: writes everything
    target = tmp_path / "fixtures" / "warp" / "freeze_at_zero.json"
    pinned = target.read_text(encoding="utf-8")
    target.write_text(pinned.replace('"hold": 0.5', '"hold": 0.6'), encoding="utf-8")
    stale = tmp_path / "fixtures" / "rng" / "old_case.json"
    stale.write_text("{}", encoding="utf-8")

    assert generator.main(["--out", str(tmp_path)]) == 1
    assert "Refusing" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") != pinned and stale.exists()  # nothing touched

    assert generator.main(["--out", str(tmp_path), "--check"]) == 1
    assert generator.main(["--out", str(tmp_path), "--accept"]) == 0
    assert target.read_text(encoding="utf-8") == pinned and not stale.exists()
    assert generator.main(["--out", str(tmp_path), "--check"]) == 0
