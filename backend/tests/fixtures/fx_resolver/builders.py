"""Builders for resolver tests and golden generation (WP2b). Pure data helpers; I/O only to read the built-in beds
from manifest.json and in `load_fixture`."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from pydantic import TypeAdapter

from src.edit_fx.contracts import Beat, EditBeats, EditStyle, FxCue, FxPlan
from src.edit_fx.resolver import ResolveInput, resolve

FIXTURES = Path(__file__).resolve().parent

# Built-in manifest shape (§12): variant counts per sound (= manifest.json, checked by test_fx_assets_manifest),
# and the built-in beds of each mood with their loop lengths, read from manifest.json.
SFX_VARIANTS = {"whoosh": 6, "impact": 7, "pop": 6, "riser": 8, "record_scratch": 6, "boom": 6, "cash_register": 5,
                "ticker": 6, "slap": 6, "stamp": 6, "glitch": 6, "tape_stop": 6}


def _manifest_beds() -> tuple[dict[str, tuple[str, ...]], dict[str, float]]:
    manifest = FIXTURES.parents[2] / "src" / "fx_assets" / "manifest.json"
    music = json.loads(manifest.read_text(encoding="utf-8"))["music"]
    beds: dict[str, tuple[str, ...]] = {}
    for entry in music:
        beds[entry["mood"]] = (*beds.get(entry["mood"], ()), entry["id"])
    return beds, {entry["id"]: float(entry["loop_seconds"]) for entry in music}


BEDS, BED_LENGTHS = _manifest_beds()
FX_TEMPLATES = ("marker_circle", "emoji_drop", "slam", "behind_head", "stat_counter",
                "kinetic_stack", "decode_glitch", "highlighter_swipe", "face_doodles")
TEMPLATES = (*FX_TEMPLATES, "default")
INTENSITIES = ("off", "subtle", "bold", "max")
PAUSE = re.compile(r"^\|(\d+(?:\.\d+)?)\|$")


def words_from(script: str, *, start: float = 0.4, gap: float = 0.08, sentence_gap: float = 0.45) -> list[dict]:
    """Words with plausible timings. `|0.9|` sets the pause before the next word."""
    words: list[dict] = []
    t = start
    pending: float | None = None
    for token in script.split():
        m = PAUSE.match(token)
        if m:
            pending = float(m.group(1))
            continue
        if words:
            prev = words[-1]["text"]
            pause = pending if pending is not None else (sentence_gap if re.search(r"[.!?]$", prev) else gap)
            t = words[-1]["end"] + pause
        pending = None
        length = 0.18 + 0.03 * len(token)
        words.append({"id": f"w{len(words)}", "start": round(t, 3), "end": round(t + length, 3), "text": token})
    return words


def find(words: Sequence[dict], phrase: str, after: int = 0) -> tuple[int, int]:
    norm = lambda s: re.sub(r"[^\w$%.,'!?]", "", s.lower())  # noqa: E731
    needle = [norm(t) for t in phrase.split()]
    for i in range(after, len(words)):
        if [norm(w["text"]) for w in words[i:i + len(needle)]] == needle:
            return i, i + len(needle) - 1
    raise AssertionError(f"phrase not found: {phrase!r}")


def beat(words: Sequence[dict], phrase: str, kind: str, *, strength: int = 2, tone: str = "neutral",
         bid: str | None = None, after: int = 0, **extra: Any) -> Beat:
    i, j = find(words, phrase, after)
    span = words[i:j + 1]
    return Beat(id=bid or "x", kind=kind, start=span[0]["start"], end=span[-1]["end"],
                text=" ".join(w["text"] for w in span)[:200], strength=strength, tone=tone, **extra)


def beats_of(beats: Iterable[Beat], *, renumber: bool = True, **meta: Any) -> EditBeats:
    ordered = sorted(beats, key=lambda b: (b.start, b.end, b.kind))
    if renumber:
        ordered = [b.model_copy(update={"id": f"b{i}"}) for i, b in enumerate(ordered)]
    return EditBeats(words_hash="test", beats=tuple(ordered), **meta)


def make_input(words: Sequence[dict], beats: EditBeats, *, duration: float | None = None, **kw: Any) -> ResolveInput:
    if duration is None:
        duration = round((words[-1]["end"] if words else 0.0) + 0.8, 3)
    params: dict[str, Any] = {"sfx_variants": SFX_VARIANTS, "beds": BEDS, "bed_lengths": BED_LENGTHS}
    params.update(kw)
    return ResolveInput(words=tuple(words), beats=beats, duration=duration, **params)


def load_input(data: dict) -> tuple[ResolveInput, int]:
    """`<name>.input.json` -> (ResolveInput, plan seed)."""
    cues = TypeAdapter(list[FxCue]).validate_python(data.get("user_cues", []))
    inp = ResolveInput(
        words=tuple({k: w[k] for k in ("id", "start", "end", "text")} for w in data["words"]),
        beats=EditBeats.model_validate(data["beats"]),
        duration=float(data["duration"]),
        cuts=tuple(data["cuts"]),
        passage_breaks=tuple(data["passage_breaks"]),
        broll_windows=tuple(tuple(w) for w in data["broll_windows"]),
        hook_window=tuple(data["hook_window"]) if data.get("hook_window") else None,
        max_clip_seconds=data.get("max_clip_seconds"),
        sfx_variants=dict(data["sfx_variants"]),
        beds={k: tuple(v) for k, v in data["beds"].items()},
        bed_lengths=dict(data["bed_lengths"]),
        reactions_available=bool(data.get("reactions_available")),
        user_cues=tuple(cues),
        suppressed=frozenset(data.get("suppressed", [])),
    )
    return inp, int(data["seed"])


def load_fixture(name: str) -> tuple[ResolveInput, int]:
    return load_input(json.loads((FIXTURES / f"{name}.input.json").read_text()))


def golden(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.golden.json").read_text())


def plan_sha(plan: FxPlan) -> str:
    blob = json.dumps(plan.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def describe(cue) -> str:
    detail = {
        "accent": lambda c: f"{c.variant} {c.text!r}" + (f" {c.emoji}" if c.emoji else "")
        + (f" {c.doodle}" if c.variant == "doodle" else "") + (f" ={c.value}" if c.value is not None else ""),
        "zoom": lambda c: f"{c.mode} {c.scale}",
        "shake": lambda c: f"amp {c.amp}",
        "flash": lambda c: f"peak {c.peak}",
        "glitch": lambda c: f"amount {c.amount}",
        "desaturate": lambda c: f"{c.amount}/{c.dim}",
        "transition": lambda c: c.style,
        "sfx": lambda c: f"{c.sound}#{c.variant} {c.gain_db:+g}dB",
        "bleep": lambda c: f"cover {c.cover}",
        "speed": lambda c: f"x{c.rate}",
        "freeze": lambda c: f"hold {c.hold} {c.label!r}",
        "sticker": lambda c: f"{c.sticker} {c.text!r}",
        "reaction": lambda c: repr(c.query),
    }[cue.kind](cue)
    return f"{cue.start:7.3f}-{cue.end:7.3f} {cue.id:<16} {cue.kind:<10} {detail}"


def goldens_for(data: dict) -> dict[str, Any]:
    inp, seed = load_input(data)
    out: dict[str, Any] = {}
    for template in TEMPLATES:
        for intensity in INTENSITIES:
            if intensity == "off" and template == "default":
                continue  # legacy + off never resolves (§8.6)
            plan = resolve(inp, EditStyle(intensity=intensity), template, seed)
            out[f"{template}/{intensity}"] = {
                "sha256": plan_sha(plan),
                "music": None if plan.music is None else f"{plan.music.bed} {plan.music.gain_db}/{plan.music.duck_db}",
                "grade": plan.grade,
                "hook_title_suppressed": plan.hook_title_suppressed,
                "cues": [describe(c) for c in plan.cues],
            }
    return out
