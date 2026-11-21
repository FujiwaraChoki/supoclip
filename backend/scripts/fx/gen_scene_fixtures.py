#!/usr/bin/env python3
"""Generate the studio scene fixtures (``shared/fx/fixtures/scene/*.json``) from the Python studio renderer.

Each case is a studio document with dynamic edits and the session facts the server export uses; ``expected`` is the
``FxScene`` the export hands fx-core (``editor_fx_render.studio_scene``: cues mapped through the segment cuts, E
words with their run, the FX caption look, the face track resampled onto E and placed with ``picture_rect``, the
behind-text matte windows, B-roll windows and the hook window). fx-core's ``studioScene`` (TypeScript) must
produce the same scene from the same input; pytest (``tests/unit/test_editor_fx_render.py``) checks Python.

File shape: ``{"family": "scene", "case", "fn": "studio_scene", "input": {...}, "expected": FxScene}`` with input

- ``document``: the ``EditDocument`` JSON (``fx`` included, S times);
- ``source``: ``{"width", "height"}`` of the base video (``layout.mp4`` or ``clean.mp4``);
- ``base``: ``"layout"`` | ``"clean"``: the base video the render frames;
- ``faceTrack``: the session's ``{"base", "track"}`` or null (used only when its base equals ``base``);
- ``matte``: whether the session's ``matte.mp4`` is available for that base (behind windows then have a person);
- ``broll``: whether the render has its B-roll footage (else no B-roll windows);
- ``captionTemplate``: the server template fields the FX caption look reads (``caption_looks`` carries them).

Floats compare within 1e-6. Like ``gen_fixtures.py`` it refuses to overwrite a changed file without ``--accept``.

    cd backend && .venv/bin/python scripts/fx/gen_scene_fixtures.py [--accept | --check] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from src.caption_templates import get_template  # noqa: E402
from src.editor_document import EditDocument  # noqa: E402
from src.editor_fx_render import studio_scene  # noqa: E402

DEFAULT_OUT = BACKEND.parent / "shared" / "fx" / "fixtures" / "scene"
TEMPLATE_KEYS = ("id", "font_family", "highlight_color", "stroke_color", "stroke_width", "shadow", "background_color")


def cue(kind: str, cue_id: str, start: float, end: float | None = None, **fields: Any) -> dict:
    return {"kind": kind, "id": cue_id, "start": start, "end": start if end is None else end, "seed": 11, **fields}


WORDS = [
    {"id": "w0", "start": 0.10, "end": 0.45, "text": "This"},
    {"id": "w1", "start": 0.45, "end": 0.90, "text": "changed", "highlight": True},
    {"id": "w2", "start": 0.90, "end": 1.40, "text": "everything"},
    {"id": "w3", "start": 1.60, "end": 2.00, "text": "for"},
    {"id": "w4", "start": 2.00, "end": 2.50, "text": "me,"},
    {"id": "w5", "start": 2.70, "end": 3.10, "text": "damn"},
    {"id": "w6", "start": 3.10, "end": 3.60, "text": "seriously"},
    {"id": "w7", "start": 3.80, "end": 4.40, "text": "10x"},
    {"id": "w8", "start": 4.40, "end": 5.00, "text": "faster."},
]

BASE_CUES = [
    cue("zoom", "b1.zoom", 0.45, 0.90, mode="punch", scale=1.18, anchor="face", beat="b1"),
    cue("sfx", "b1.sfx", 0.45, sound="boom", beat="b1"),
    cue("flash", "b2.flash", 2.00, 2.20, beat="b2"),
    cue("shake", "b2.shake", 2.00, 2.40, amp=14, beat="b2"),
    cue("bleep", "b3.bleep", 2.70, 3.10, cover="bar", beat="b3"),
    cue("accent", "b4.counter", 3.80, 4.40, variant="counter", text="10x", value=10, suffix="x", beat="b4"),
    cue("transition", "g.cut1", 3.70, style="whip"),
    cue("glitch", "b5.glitch", 4.40, 4.70, amount=0.6, beat="b5"),
]


def fx(**changes: Any) -> dict:
    data = {"style": {"intensity": "bold"}, "seed": 2961888143, "beats": [], "cues": BASE_CUES}
    data.update(changes)
    return data


def document(segments, *, template="marker_circle", fx_data=None, **changes: Any) -> dict:
    doc = {
        "segments": [{"id": sid, "start": start, "end": end} for sid, start, end in segments],
        "words": WORDS,
        "captions": {"template": template},
        "hookTitle": "He tried it for 30 days",
        "fx": fx_data if fx_data is not None else fx(),
        **changes,
    }
    return EditDocument.model_validate(doc).model_dump(mode="json")


def face_track(base: str, *, duration: float = 6.0, fps: float = 6.0, drop: tuple[float, float] | None = None) -> dict:
    """A face drifting right over time (x grows 0.02 per second), with landmarks; none inside ``drop``."""
    samples = []
    for k in range(int(duration * fps) + 1):
        t = round(k / fps, 6)
        if drop and drop[0] <= t < drop[1]:
            samples.append([t, []])
            continue
        x = round(0.30 + 0.02 * t, 6)
        row = [x, 0.25, 0.30, 0.22, 0.93, x + 0.09, 0.32, x + 0.21, 0.32, x + 0.15, 0.38, x + 0.10, 0.42, x + 0.20,
               0.42]
        samples.append([t, [[round(v, 6) for v in row]]])
    return {"base": base, "track": {"version": 1, "space": "picture", "fps": fps, "samples": samples}}


def broll_clip(clip_id: str, start: float, end: float) -> dict:
    return {"id": clip_id, "start": start, "end": end, "query": "city", "footage": {
        "id": 12345, "width": 1080, "height": 1920, "duration": 10,
        "fileUrl": "https://videos.pexels.com/video-files/12345/city.mp4"}}


def cases() -> dict[str, dict]:
    vertical = {"width": 1080, "height": 1920}
    landscape = {"width": 1920, "height": 1080}
    face_clean = face_track("clean")
    out: dict[str, dict] = {}

    def add(name: str, doc: dict, *, source=vertical, base="clean", face=None, matte=False, broll=True) -> None:
        out[name] = {"document": doc, "source": source, "base": base, "faceTrack": face, "matte": matte,
                     "broll": broll}

    add("single_segment", document([("all", 0.0, 5.0)]))
    add("trimmed_head_and_tail", document([("mid", 0.4, 4.5)]))
    add("reordered_segments", document([("late", 3.0, 5.0), ("early", 0.0, 2.2)]))
    add("repeated_segment", document([("first", 0.0, 2.5), ("again", 0.3, 1.5)]))
    add("cut_removes_bleep", document([("a", 0.0, 2.6), ("b", 3.2, 5.0)]))
    add("contiguous_split_merges", document([("a", 0.0, 1.0), ("a2", 1.0, 3.0), ("b", 3.5, 5.0)]))
    add("disabled_kinds", document([("all", 0.0, 5.0)], fx_data=fx(disabled=["sfx", "zoom", "glitch"])))
    add("legacy_template_keeps_ass_captions", document([("all", 0.0, 5.0)], template="hormozi"))
    add("captions_disabled", document([("all", 0.0, 5.0)], captions={"template": "slam", "enabled": False}))
    add("caption_placements", document(
        [("first", 0.0, 2.5), ("second", 2.6, 5.0)],
        captions={"template": "highlighter_swipe", "size": 60, "y": 0.7, "uppercase": True, "wordsPerLine": 3,
                  "color": "#FFFFFF", "accent": "#22D3EE"},
        textTransforms={"captions": {"x": 0.0, "y": -0.1, "scale": 1.25}},
        captionTransforms={'["first","w0"]': {"x": 0.1, "y": -0.05, "scale": 1.5},
                           '["second","w5"]': {"x": 0.0, "y": 0.1, "scale": 1.0, "size": 90}},
    ))
    add("face_track_follows_cut", document([("late", 3.0, 5.0), ("early", 0.5, 2.0)]), face=face_clean)
    add("face_track_gap_and_ends", document([("a", 0.0, 2.0), ("b", 4.8, 6.0)]),
        face=face_track("clean", drop=(1.0, 2.0)))
    add("face_track_other_base_ignored", document([("all", 0.0, 5.0)]), face=face_track("layout"))
    add("face_track_on_layout", document([("all", 0.5, 4.5)], framing={"fit": "auto", "aspect": "vertical"}),
        base="layout", face=face_track("layout"))
    behind = fx(cues=[cue("accent", "b6.behind", 1.6, 2.5, variant="behind", text="FOR ME"),
                      cue("accent", "b7.doodle", 0.45, 1.40, variant="doodle", doodle="horns")])
    # The intensity caption looks of the listed styles (fx_caption_style_from_document look=…).
    add("look_max_emoji_drop", document([("all", 0.0, 5.0)], template="emoji_drop",
                                        fx_data=fx(style={"intensity": "max"}),
                                        captions={"template": "emoji_drop", "size": 84, "font": "THEBOLDFONT"}))
    add("look_subtle_highlighter", document([("all", 0.0, 5.0)], template="highlighter_swipe",
                                            fx_data=fx(style={"intensity": "subtle"}),
                                            captions={"template": "highlighter_swipe", "size": 52, "y": 0.68,
                                                      "wordsPerLine": 4}))
    add("look_serious_is_subtle", document([("all", 0.0, 5.0)], template="behind_head",
                                           fx_data=fx(style={"intensity": "max"}, sensitivity="serious")))
    add("behind_with_matte", document([("a", 0.0, 2.6), ("b", 3.6, 5.0)], template="behind_head", fx_data=behind),
        face=face_clean, matte=True)
    add("behind_without_matte", document([("a", 0.0, 2.6)], template="behind_head", fx_data=behind), matte=False)
    warp = fx(cues=[*BASE_CUES, cue("freeze", "b8.freeze", 2.20, hold=0.6, label="wait"),
                    cue("speed", "b9.speed", 4.40, 5.00, rate=2.0)])
    add("warp_across_cuts", document([("a", 0.0, 3.0), ("b", 4.0, 5.0)], fx_data=warp))
    add("square_contain_framing", document([("all", 0.0, 5.0)], framing={"aspect": "square", "fit": "contain",
                                                                          "zoom": 1.0, "x": 0.5, "y": 0.5}),
        source=landscape, face=face_track("clean"))
    add("landscape_original_cover_zoom", document(
        [("all", 0.0, 5.0)], framing={"aspect": "original", "fit": "cover", "zoom": 1.4, "x": 0.2, "y": 0.7}),
        source=landscape, face=face_track("clean"))
    add("broll_windows", document([("a", 0.0, 2.5), ("b", 3.0, 5.0)], broll=[broll_clip("r1", 1.5, 3.5)]))
    add("broll_footage_missing", document([("all", 0.0, 5.0)], broll=[broll_clip("r1", 1.5, 3.5)]), broll=False)
    add("hook_timing_and_globals", document(
        [("all", 0.0, 5.0)], hookTiming={"start": 0.5, "end": 2.0},
        fx_data=fx(grade="teal_orange", grain=0.3, progressBar=True, loopEnding="crossfade",
                   hookTitleSuppressed=True)))
    add("custom_progress_color", document(
        [("all", 0.0, 5.0)], fx_data=fx(progressBar=True, progressColor="#22cc88")))
    add("caption_global_x_offset", document(
        [("first", 0.0, 2.5), ("second", 2.6, 5.0)], captions={"template": "slam", "y": 0.72},
        textTransforms={"captions": {"x": -0.18, "y": 0.05, "scale": 0.9}},
        captionTransforms={'["second","w5"]': {"x": 0.12, "y": -0.2, "scale": 1.1}}))
    add("no_hook_title", document([("all", 0.0, 5.0)], hookTitle=""))
    # The hook title card (scene.hook): the draft's transform, its own timing (no trimming at cuts), a font the user
    # picked on a listed style, an all-caps template, and a title with stray whitespace.
    add("hook_moved_and_scaled", document(
        [("all", 0.0, 5.0)], textTransforms={"hook": {"x": 0.08, "y": 0.31, "scale": 1.4}},
        hookTiming={"start": 0.4, "end": 3.2}))
    add("hook_listed_style_user_font", document(
        [("all", 0.0, 5.0)], template="highlighter_swipe",
        captions={"template": "highlighter_swipe", "font": "TikTokSans-Regular", "accent": "#34fd87", "uppercase": True},
        hookTitle="  90%  of guests\tbecome friends "))
    add("hook_listed_style_look_font", document(
        [("all", 0.0, 5.0)], template="emoji_drop", captions={"template": "emoji_drop", "font": "THEBOLDFONT"},
        fx_data=fx(style={"intensity": "max"})))
    add("hook_uppercase_template", document(
        [("all", 0.0, 5.0)], captions={"template": "slam", "uppercase": True, "font": "THEBOLDFONT"}))
    # Interview caption rows (S): pair → single → fit → pair. They place the look captions while the captions keep
    # their automatic position, mapped through the cuts like B-roll.
    rows = {"y": 0.5, "rows": [{"start": 0.0, "end": 1.5, "y": 0.5}, {"start": 1.5, "end": 2.6, "y": 0.65},
                               {"start": 2.6, "end": 3.7, "y": 0.76}, {"start": 3.7, "end": 5.0, "y": 0.5}]}
    look = {"template": "highlighter_swipe", "y": 0.5, "size": 72, "wordsPerLine": 3, "font": "Poppins-ExtraBold"}

    def interview(segments, **changes):
        return document(segments, template="highlighter_swipe", captions={**look, **changes.pop("captions", {})},
                        captionRows=changes.pop("captionRows", rows), **changes)

    add("interview_rows", interview([("all", 0.0, 5.0)]))
    add("interview_rows_reordered_and_trimmed", interview([("late", 2.0, 4.6), ("early", 0.2, 1.9)]))
    add("interview_rows_repeated_segment", interview([("first", 0.0, 3.0), ("again", 1.0, 2.0)]))
    add("interview_rows_warp", interview([("a", 0.0, 3.0), ("b", 3.6, 5.0)], fx_data=warp))
    add("interview_rows_with_placement", interview(
        [("all", 0.0, 5.0)], captionTransforms={'["all","w3"]': {"x": 0.0, "y": 0.2, "scale": 1.0}}))
    add("interview_rows_pinned_by_caption_y", interview([("all", 0.0, 5.0)], captions={"y": 0.42}))
    add("interview_rows_pinned_by_layer", interview(
        [("all", 0.0, 5.0)], textTransforms={"captions": {"x": 0.0, "y": 0.1, "scale": 1.0}}))
    add("interview_rows_layer_scale_keeps_rows", interview(
        [("all", 0.0, 5.0)], textTransforms={"captions": {"x": 0.05, "y": 0.0, "scale": 1.2}}))
    add("interview_rows_square_frame_off", interview(
        [("all", 0.0, 5.0)], framing={"aspect": "square", "fit": "cover", "zoom": 1.0, "x": 0.5, "y": 0.5}))
    add("interview_rows_zoomed_frame_off", interview(
        [("all", 0.0, 5.0)], framing={"aspect": "vertical", "fit": "cover", "zoom": 1.3, "x": 0.5, "y": 0.5}))
    add("interview_rows_template_look_off", document(
        [("all", 0.0, 5.0)], template="marker_circle", captions={"template": "marker_circle", "y": 0.5},
        captionRows=rows))
    add("interview_rows_captions_disabled", interview([("all", 0.0, 5.0)], captions={"enabled": False}))
    add("interview_rows_approx_timing", interview([("all", 0.0, 5.0)], fx_data=fx(timing="approx")))
    add("approx_timing_keeps_words", document([("all", 0.0, 5.0)], fx_data=fx(timing="approx")))
    return out


def scene_case(name: str, inp: dict) -> dict:
    doc = EditDocument.model_validate(inp["document"])
    template = get_template(doc.captions.template)
    inp = {**inp, "captionTemplate": {key: template.get(key) for key in TEMPLATE_KEYS}}
    scene = studio_scene(doc, source_w=inp["source"]["width"], source_h=inp["source"]["height"], base=inp["base"],
                         face_track=inp["faceTrack"], matte=inp["matte"], broll=inp["broll"])
    return {"family": "scene", "case": name, "fn": "studio_scene", "input": inp,
            "expected": scene.model_dump(mode="json")}


def render(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def build_outputs() -> dict[str, str]:
    """File name → content."""
    return {f"{name}.json": render(scene_case(name, inp)) for name, inp in cases().items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--accept", action="store_true", help="overwrite fixtures whose content changes")
    parser.add_argument("--check", action="store_true", help="only report differences; exit 1 when any")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder (default: shared/fx/fixtures/scene)")
    args = parser.parse_args(argv)

    outputs = build_outputs()
    changed = [name for name, content in outputs.items()
               if (args.out / name).exists() and (args.out / name).read_text(encoding="utf-8") != content]
    missing = [name for name in outputs if not (args.out / name).exists()]
    stale = sorted(path.name for path in args.out.glob("*.json") if path.name not in outputs) \
        if args.out.is_dir() else []
    if args.check:
        for name in changed + missing + stale:
            print(f"differs: {name}")
        return 1 if changed or missing or stale else 0
    if (changed or stale) and not args.accept:
        for name in changed:
            print(f"would change: {name}", file=sys.stderr)
        for name in stale:
            print(f"would remove: {name}", file=sys.stderr)
        print("Refusing to overwrite pinned fixtures; re-run with --accept if the change is intended.", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    for name in changed + missing:
        (args.out / name).write_text(outputs[name], encoding="utf-8")
    for name in stale:
        (args.out / name).unlink()
    print(f"{len(outputs)} scene fixtures: {len(missing)} new, {len(changed)} updated, {len(stale)} removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
