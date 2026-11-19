"""Versioned, non-destructive clip drafts and deterministic render instructions.

Originals are never modified. Drafts, their revisions and render jobs are rows
(see ``editor_state``). A session's directory under ``TEMP_DIR/editor`` only
caches its media on the machine that is rendering it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, NamedTuple, Optional, Sequence
import hashlib
import json
import os
import subprocess
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from .edit_fx.contracts import (Beat, EditStyle, FxCue, FxKind, GradeId, Mood, MusicBed, Sensitivity, Timing)
from .edit_fx.contracts import EDIT_FX_VERSION, is_fx_template

BROLL_MIN_SECONDS = 0.2
BROLL_CONTINUATION_MIN_SECONDS = 0.04
BROLL_FADE_SECONDS = 0.25
TIME_EPSILON = 1e-9


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Segment(Model):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end - self.start < 0.04 - TIME_EPSILON:
            raise ValueError("Each segment must be at least one frame long")
        return self


class Word(Model):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=120)
    highlight: bool = False

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Caption end must follow its start")
        return self


class CaptionStyle(Model):
    enabled: bool = True
    font: Literal[
        "TikTokSans-Regular", "TikTokSans-Bold", "Poppins-ExtraBold", "THEBOLDFONT"
    ] = "Poppins-ExtraBold"
    # 1080-wide units; 132 matches the generation renderer's largest caption.
    size: int = Field(default=54, ge=20, le=132)
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")
    accent: str = Field(default="#FACC15", pattern=r"^#[0-9a-fA-F]{6}$")
    y: float = Field(default=0.78, ge=0.1, le=0.9)
    background: bool = True
    wordsPerLine: int = Field(default=4, ge=1, le=8)
    # Colour the word being spoken with the accent, as the generated clip does.
    karaoke: bool = False
    uppercase: bool = False
    # Caption template the clip was generated with; supplies the animation, outline,
    # word pill and emphasis styling that the fields above don't expose.
    template: str = Field(default="default", min_length=1, max_length=50)


class Framing(Model):
    aspect: Literal["vertical", "square", "original"] = "vertical"
    fit: Literal["contain", "cover", "auto"] = "cover"
    zoom: float = Field(default=1, ge=1, le=3)
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)


class Effects(Model):
    brightness: float = Field(default=100, ge=40, le=180)
    contrast: float = Field(default=100, ge=40, le=180)
    saturation: float = Field(default=100, ge=0, le=220)
    blur: float = Field(default=0, ge=0, le=8)
    hue: float = Field(default=0, ge=-180, le=180)


class BRollFootage(Model):
    """A stock video rendition. Only Pexels' video CDN is ever downloaded."""

    provider: Literal["pexels"] = "pexels"
    id: int = Field(gt=0)
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    duration: float = Field(gt=0, le=3600)
    fileUrl: str = Field(pattern=r"^https://videos\.pexels\.com/[^\s]{1,500}$")
    pageUrl: str = Field(default="", max_length=500)
    thumbnail: str = Field(default="", max_length=500)
    credit: str = Field(default="", max_length=200)

    @property
    def key(self) -> str:
        return f"{self.id}/{self.width}x{self.height}"


class BRollClip(Model):
    """Stock footage shown over the clip from ``start`` to ``end`` (clean.mp4 time).

    Like caption words, it lives on the source timeline, so cuts, reorders and
    duplicated segments carry it along. ``offset`` is where in the footage it begins.
    """

    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    offset: float = Field(default=0, ge=0, le=3600)
    query: str = Field(default="", max_length=120)
    footage: BRollFootage
    # Combined cuts remain separate footage ranges, without adding dissolves at the join.
    fadeIn: bool = True
    fadeOut: bool = True
    fadeDuration: float | None = Field(default=None, gt=0, le=BROLL_FADE_SECONDS)

    @model_validator(mode="after")
    def ordered(self):
        minimum = BROLL_MIN_SECONDS if self.fadeIn and self.fadeOut else BROLL_CONTINUATION_MIN_SECONDS
        if self.end - self.start < minimum - TIME_EPSILON:
            raise ValueError(f"B-roll must be at least {minimum} seconds long")
        return self


class HookTiming(Model):
    start: float = Field(ge=0, le=86400)
    end: float = Field(gt=0, le=86400)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Title end must follow its start")
        return self


class TextTransform(Model):
    x: float = Field(default=0, ge=-1, le=1)
    y: float = Field(default=0, ge=-1, le=1)
    scale: float = Field(default=1, ge=0.25, le=4)


class CaptionTransform(TextTransform):
    size: float | None = Field(default=None, ge=5, le=528)


class CaptionRowDoc(Model):
    """One interview shot's caption centre, on the draft's source clock S (``edit_fx.contracts.CaptionRow``)."""
    start: float = Field(ge=0, le=86400)
    end: float = Field(ge=0, le=86400)
    y: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("Caption row end must follow its start")
        return self


class CaptionRows(Model):
    """Per-shot caption rows of an interview clip (``edit_fx.caption_rows``), seeded when the session is prepared.

    They place the intensity-look captions only while the captions keep their automatic position: ``captions.y``
    still equals ``y`` (the position they were seeded with), the caption layer has not been moved and the framing
    is the full vertical frame (``studio_caption_rows``). A caption's own transform pins that caption."""
    y: float = Field(ge=0, le=1)
    rows: list[CaptionRowDoc] = Field(default_factory=list, max_length=400)


class DocFx(Model):                     # Model = extra="forbid", allow_inf_nan=False (existing base)
    version: Literal["fx-v1"] = "fx-v1"
    style: EditStyle = Field(default_factory=EditStyle)
    seed: int = Field(default=0, ge=0, le=0xFFFFFFFF)
    mood: Optional[Mood] = None
    sensitivity: Sensitivity = "normal"
    timing: Timing = "word"
    beats: list[Beat] = Field(default_factory=list, max_length=200)          # S (clean.mp4) seconds
    cues: list[FxCue] = Field(default_factory=list, max_length=400)          # S seconds
    suppressed: list[str] = Field(default_factory=list, max_length=400)      # auto cue ids the user deleted
    disabled: list[FxKind] = Field(default_factory=list)                     # kinds switched off by toggles
    music: Optional[MusicBed] = None                                         # global, F-anchored
    grade: GradeId = "none"
    grain: float = Field(default=0, ge=0, le=1)
    progressBar: bool = False
    progressColor: str = Field(default="#FF7A1A", pattern=r"^#[0-9a-fA-F]{6}$")
    loopEnding: Literal["none", "crossfade"] = "none"
    hookTitleSuppressed: bool = False


class EditDocument(Model):
    version: Literal[1] = 1
    segments: list[Segment] = Field(min_length=1, max_length=100)
    words: list[Word] = Field(default_factory=list, max_length=5000)
    captions: CaptionStyle = Field(default_factory=CaptionStyle)
    framing: Framing = Field(default_factory=Framing)
    effects: Effects = Field(default_factory=Effects)
    volume: float = Field(default=1, ge=0, le=2)
    muted: bool = False
    # AI headline burned into the top safe area for the first seconds, as generated.
    hookTitle: str = Field(default="", max_length=200)
    hookTiming: HookTiming | None = None
    textTransforms: dict[Literal["hook", "captions"], TextTransform] = Field(default_factory=dict)
    captionTransforms: dict[Annotated[str, Field(max_length=300)], CaptionTransform] = Field(default_factory=dict, max_length=5000)
    broll: list[BRollClip] = Field(default_factory=list, max_length=60)
    # Dynamic edits (§5.1). None <=> the key is absent: it is never serialised as null, so every
    # document without dynamic edits stays byte-identical to what older releases wrote and read.
    fx: Optional[DocFx] = None           # None ⇔ key absent. NEVER serialised as null (see rules)
    # Interview caption rows (S). None <=> the key is absent, like ``fx``: drafts without them stay byte-identical.
    captionRows: Optional[CaptionRows] = None

    @model_serializer(mode="wrap")
    def _omit_unset_fx(self, handler):   # VERBATIM: keeps every non-FX document byte-identical to today (AI-H1)
        data = handler(self)
        if data.get("fx") is None:
            data.pop("fx", None)
        if data.get("captionRows") is None:
            data.pop("captionRows", None)
        return data

    def validate_duration(self, duration: float):
        if any(s.end > duration + 0.05 for s in self.segments):
            raise ValueError("An edit extends past the original clip")
        if any(w.end > duration + 0.05 for w in self.words):
            raise ValueError("A caption extends past the original clip")
        if any(b.end > duration + 0.05 for b in self.broll):
            raise ValueError("B-roll extends past the original clip")
        if len({s.id for s in self.segments}) != len(self.segments) or len(
            {w.id for w in self.words}
        ) != len(self.words) or len({b.id for b in self.broll}) != len(self.broll):
            raise ValueError("Editor item IDs must be unique")
        ordered = sorted(self.broll, key=lambda b: b.start)
        if any(later.start < earlier.end - 0.01 for earlier, later in zip(ordered, ordered[1:])):
            raise ValueError("B-roll clips can't overlap")
        if self.fx is not None:
            _validate_fx(self.fx, duration)
        return self


def _validate_fx(fx: DocFx, duration: float) -> None:
    """The §5.1 checks on dynamic edits. The freeze hold budget is deliberately not one of them: it is enforced where
    cues are written (resolver, AI edit, the PATCH route), so a combined clip that rebases several clips' freezes
    stays valid (AR-M14)."""
    limit = duration + 0.05
    if any(b.end > limit for b in fx.beats):
        raise ValueError("A tagged moment extends past the original clip")
    if any(c.end > limit for c in fx.cues):
        raise ValueError("An effect extends past the original clip")
    if len({c.id for c in fx.cues}) != len(fx.cues) or len({b.id for b in fx.beats}) != len(fx.beats):
        raise ValueError("Effect IDs must be unique")
    if any(c.kind == "freeze" and c.start != c.end for c in fx.cues):
        raise ValueError("A freeze frame is an instant")
    speeds = sorted((c for c in fx.cues if c.kind == "speed"), key=lambda c: (c.start, c.end))
    if any(later.start < earlier.end - 1e-6 for earlier, later in zip(speeds, speeds[1:])):
        raise ValueError("Speed changes can't overlap")


def editor_dir(temp_dir: str, task_id: str, clip_id: str, filename: str) -> Path:
    # No request-controlled string is ever used as a path component.
    key = hashlib.sha256(f"{task_id}:{clip_id}:{filename}".encode()).hexdigest()
    return Path(temp_dir) / "editor" / key


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    try:
        temporary.write_text(json.dumps(value, allow_nan=False))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class RevisionConflict(ValueError):
    pass


def output_size(document: EditDocument, width: int, height: int):
    if document.framing.aspect == "square":
        return 1080, 1080
    if document.framing.aspect == "vertical":
        return 1080, 1920
    scale = min(1, 1920 / max(width, height))
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def mapped_words(document: EditDocument):
    result = []
    cursor = 0.0
    previous_end = None
    previous_words = {}
    ordered_words = sorted(document.words, key=lambda w: w.start)
    for segment in document.segments:
        continuous = previous_end is not None and abs(segment.start - previous_end) < 1e-6
        current_words = {}
        for word in ordered_words:
            start, end = max(segment.start, word.start), min(segment.end, word.end)
            if end > start and word.text.strip():
                piece = {
                    **word.model_dump(),
                    "start": cursor + start - segment.start,
                    "end": cursor + end - segment.start,
                }
                previous = previous_words.get(word.id) if continuous else None
                if previous is not None and abs(previous["end"] - piece["start"]) < 1e-6:
                    previous["end"] = piece["end"]
                    current_words[word.id] = previous
                else:
                    result.append(piece)
                    current_words[word.id] = piece
        cursor += segment.end - segment.start
        previous_end = segment.end
        previous_words = current_words
    return result


def continuous_segments(segments: list[Segment]) -> list[Segment]:
    result = []
    for segment in segments:
        if result and abs(result[-1].end - segment.start) < TIME_EPSILON:
            result[-1] = result[-1].model_copy(update={"end": segment.end})
        else:
            result.append(segment)
    return result


def caption_key(document: EditDocument, words: list[dict]) -> str:
    cursor = 0.0
    segment = document.segments[-1]
    for segment in continuous_segments(document.segments):
        cursor += segment.end - segment.start
        if words[0]["start"] < cursor - TIME_EPSILON:
            break
    return json.dumps([segment.id, words[0]["id"]], separators=(",", ":"), ensure_ascii=False)


def mapped_broll(document: EditDocument) -> list[dict]:
    """B-roll pieces on the output timeline, following segments like captions do.

    A clip cut by a segment boundary becomes several pieces; only a piece that
    starts (or ends) where the clip does fades in (or out), so a cutaway keeps
    playing across a cut instead of dissolving at it.
    """
    pieces = []
    cursor = 0.0
    ordered = sorted(document.broll, key=lambda b: b.start)
    for segment in document.segments:
        for clip in ordered:
            start, end = max(segment.start, clip.start), min(segment.end, clip.end)
            if end - start >= BROLL_CONTINUATION_MIN_SECONDS - TIME_EPSILON:
                pieces.append(
                    {
                        "id": clip.id,
                        "footage": clip.footage,
                        "query": clip.query,
                        "start": cursor + start - segment.start,
                        "duration": end - start,
                        "offset": clip.offset + start - clip.start,
                        "fade_in": clip.fadeIn and start - clip.start < 0.02,
                        "fade_out": clip.fadeOut and clip.end - end < 0.02,
                        "fade_duration": clip.fadeDuration or min(BROLL_FADE_SECONDS, (clip.end - clip.start) / 3),
                    }
                )
        cursor += segment.end - segment.start
    return pieces


# --------------------------------------------------------------------------- dynamic edits (§5.1, §5.5, §14.1)

# A hook title over a clip no longer than this covers the whole clip (mirrors media.common.HOOK_TITLE_MIN_SECONDS;
# media.common imports the video pipeline, which the AI worker must not load).
HOOK_TITLE_SECONDS = 4.0
HOOK_TITLE_MIN_SECONDS = 1.5
FX_MAX_HOLD_SECONDS = 2.5          # Σ freeze hold, absolute (FreezeCue.hold upper bound)
FX_PASSAGE_GAP_SECONDS = 3.0       # a junction skipping this much source is a new passage (AP-M2)


class NeedsPlan(ValueError):
    """An active intensity with no tagged beats: the studio must plan beats first (409 ``NEEDS_PLAN``)."""


def edit_duration(document: EditDocument) -> float:
    """The E length of a draft before any time warp."""
    return sum(s.end - s.start for s in document.segments)


def fx_segments(document: EditDocument) -> list[tuple[str, float, float]]:
    """``(id, s_start, s_end)`` in play order, continuous-merged: the ``segments`` argument of ``edit_fx.mapping``."""
    return [(s.id, s.start, s.end) for s in continuous_segments(document.segments)]


def mapped_fx(document: EditDocument) -> list[FxCue]:
    """The draft's enabled cues on the E clock (§5.1), mirroring fx-core's ``mappedFx``."""
    from .edit_fx.mapping import mapped_cues

    if document.fx is None:
        return []
    disabled = set(document.fx.disabled)
    return mapped_cues(fx_segments(document), [c for c in document.fx.cues if c.kind not in disabled])


# captions.y within this of the seeded position still counts as the automatic position (the inspector rounds).
CAPTION_ROWS_Y_TOLERANCE = 5e-4


def studio_caption_rows(document: EditDocument) -> list:
    """The draft's interview caption rows on the E clock (``edit_fx.caption_rows.mapped_caption_rows``), or [] when
    they do not apply (== fx-core ``studioCaptionRows``): no rows, captions off or not an intensity look, the user
    moved the captions (``captions.y`` changed or the caption layer moved vertically) or the frame is not the full
    vertical picture the rows were measured on."""
    from .edit_fx.caption_rows import mapped_caption_rows
    from .edit_fx.contracts import CaptionRow, caption_look_for

    rows = document.captionRows
    captions = document.captions
    if rows is None or not rows.rows or not captions.enabled:
        return []
    fx = document.fx
    if caption_look_for(captions.template, fx.style.intensity if fx is not None else "off",
                        fx.sensitivity if fx is not None else "normal") == "template":
        return []
    layer = document.textTransforms.get("captions")
    if abs(captions.y - rows.y) > CAPTION_ROWS_Y_TOLERANCE or (layer is not None and layer.y != 0):
        return []
    if document.framing.aspect != "vertical" or document.framing.zoom != 1:
        return []
    stored = [CaptionRow(start=r.start, end=r.end, y=r.y) for r in rows.rows]
    return mapped_caption_rows(fx_segments(document), stored)


def document_needs_fx(document: EditDocument) -> bool:
    """True when a render must take the dynamic-edits path: an FX caption template, or dynamic edits with cues, music
    or a retention/colour global. An inert ``DocFx`` (the user removed every edit) renders the legacy way."""
    if is_fx_template(document.captions.template):
        return True
    fx = document.fx
    if fx is None:
        return False
    if fx.music is not None or fx.grade != "none" or fx.grain > 0 or fx.progressBar or fx.loopEnding != "none":
        return True
    return bool(mapped_fx(document))


def document_fx_on(document: EditDocument | None) -> bool:
    """Dynamic edits are switched on in this draft: an active intensity, an FX caption template, or anything that
    makes the render take the FX path. The feature flag gates only a change from off to on (§0.4.8)."""
    if document is None:
        return False
    if document.fx is not None and document.fx.style.active:
        return True
    return document_needs_fx(document)


def turns_fx_on(before: EditDocument | None, after: EditDocument) -> bool:
    """``after`` switches dynamic edits on relative to the stored ``before`` (§0.4.8): edits appear on a draft that had
    none, or, on one that already has some, the template goes from legacy to FX or the intensity from off to active
    (the same transitions ``POST /tasks/{id}/settings`` gates). An unchanged or reduced FX state never counts, so the
    kill switch never blocks a font change on a clip that already has edits."""
    if not document_fx_on(after):
        return False
    if before is None or not document_fx_on(before):
        return True
    new_template = is_fx_template(after.captions.template) and not is_fx_template(before.captions.template)
    new_style = (after.fx is not None and after.fx.style.active) and not (
        before.fx is not None and before.fx.style.active
    )
    return new_template or new_style


def fx_hold_budget(duration: float) -> float:
    """Most freeze hold a draft of ``duration`` E seconds may add when cues are written (§5.1, G14)."""
    return min(1.6, 0.04 * max(0.0, duration), FX_MAX_HOLD_SECONDS)


def mapped_hold_total(document: EditDocument) -> float:
    """Σ hold of the freezes that play (a freeze in a repeated segment plays, and holds, twice)."""
    return sum(float(c.hold) for c in mapped_fx(document) if c.kind == "freeze")


def studio_fx_seed(task_id: str, clip_id: str) -> int:
    """Seed for a draft that has no plan yet (a generated clip's draft keeps its plan's seed)."""
    from .edit_fx.rng import fnv1a32

    return fnv1a32(f"{task_id}|{clip_id}|{EDIT_FX_VERSION}")


def studio_hook_window(document: EditDocument, duration: float) -> tuple[float, float] | None:
    """Where the hook title burns, in E (the resolver keeps accents out of the top band there)."""
    if not document.hookTitle.strip() or duration <= 0:
        return None
    if document.hookTiming is not None:
        start = max(0.0, min(document.hookTiming.start, max(0.0, duration - 0.04)))
        return start, max(start + min(0.04, duration), min(document.hookTiming.end, duration))
    if duration <= HOOK_TITLE_MIN_SECONDS:
        return 0.0, max(0.5, duration)
    return 0.12, min(HOOK_TITLE_SECONDS, max(HOOK_TITLE_MIN_SECONDS, duration - 0.25))


def studio_junctions(segments: Sequence[tuple[str, float, float]]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """``(cuts, passage_breaks)`` in E for continuous-merged segments (§3.2 ``ResolveInput``).

    Every junction is a cut (continuous segments are already joined). A junction is also a passage break when it
    skips at least 3 s of source or plays earlier source again (a reorder or a repeat).
    """
    cuts: list[float] = []
    breaks: list[float] = []
    cursor = 0.0
    for index, (_, start, end) in enumerate(segments):
        if index:
            cuts.append(round(cursor, 6))
            gap = start - segments[index - 1][2]
            if gap >= FX_PASSAGE_GAP_SECONDS - TIME_EPSILON or gap < -TIME_EPSILON:
                breaks.append(round(cursor, 6))
        cursor += end - start
    return tuple(cuts), tuple(breaks)


def _reaction_ideas(segments, cues: Sequence[FxCue]):
    """Reaction ideas recovered from the draft's own reaction cues, so a re-resolve keeps the GIFs it had."""
    from .edit_fx.contracts import ReactionIdea
    from .edit_fx.mapping import mapped_cues

    ideas = []
    for cue in mapped_cues(segments, [c for c in cues if c.kind == "reaction" and c.origin == "auto"]):
        if "@" in cue.id:
            continue
        ideas.append(ReactionIdea(at=round(cue.start, 4), query=cue.query[:40] or "reaction"))
    return tuple(ideas[:3])


def resolve_document_fx(document: EditDocument, style: EditStyle, *, seed: int,
                        layout_has_zoompan: bool) -> EditDocument:
    """Re-resolve a draft's auto cues for ``style`` (§5.5): pure, no I/O beyond the built-in manifest.

    Resolves in E (AP-H9): words and beats are mapped through the draft's segments, junctions become cuts, budgets
    and quiet zones are evaluated on the E length, and the user's own cues are fixed input. The result goes back to
    S through ``inverse_mapped_cues`` (split at junctions, repeats once). User cues are kept exactly as stored, and
    ``suppressed`` auto ids never come back. Baseline pushes are added only when the picture has no baked Ken Burns
    zoom (AR-M9). Raises ``NeedsPlan`` for an active intensity without beats; style only (an FX caption template
    with intensity off) uses heuristic beats on the fly.
    """
    from .edit_fx import assets, baseline, resolver
    from .edit_fx import reactions as fx_reactions
    from .edit_fx.beats import heuristic_beats, words_with_timing
    from .edit_fx.contracts import EditBeats
    from .edit_fx.jobs import fx_active
    from .edit_fx.mapping import inverse_mapped_cues, mapped_beats, mapped_cues, mapped_words_e

    current = document.fx or DocFx()
    template = document.captions.template
    user_cues = [c for c in current.cues if c.origin == "user"]
    if not fx_active(style, template):
        if document.fx is None and style == EditStyle():
            return document
        inert = current.model_copy(update={
            "style": style, "seed": seed, "cues": user_cues, "music": None, "grade": "none", "grain": 0.0,
            "progressBar": False, "loopEnding": "none", "hookTitleSuppressed": False,
        })
        return EditDocument.model_validate({**document.model_dump(), "fx": inert.model_dump()})

    segments = fx_segments(document)
    duration = edit_duration(document)
    words = [w.model_dump() for w in sorted(document.words, key=lambda w: w.start)]
    words_e = mapped_words_e(segments, words)
    beats_s = list(current.beats)
    timing = current.timing
    if not beats_s:
        kept = [w for w in words if any(a < w["end"] and w["start"] < b for _, a, b in segments)]
        # A plan always records the mood, so "no beats and no mood" means the draft was never planned. A planned
        # draft can legitimately have no beats (nothing worth an edit, or no words): resolve it as it is.
        if style.active and kept and current.mood is None:
            raise NeedsPlan("Plan edits for this clip first")
        if not style.active:
            timed, timing = words_with_timing(kept)
            beats_s = list(heuristic_beats(timed, timing=timing).beats)
    beats_e = mapped_beats(segments, beats_s)[:200]
    hook = next((b for b in beats_e if b.kind == "hook"), None)
    edit_beats = EditBeats(
        words_hash="", source="heuristic" if not current.beats else "llm", mood=current.mood or "hype",
        sensitivity=current.sensitivity, timing=timing, hook_end=hook.end if hook else None,
        beats=tuple(beats_e), reactions=_reaction_ideas(segments, current.cues),
    )
    cuts, breaks = studio_junctions(segments)
    broll_windows = tuple(
        (round(p["start"], 6), round(p["start"] + p["duration"], 6)) for p in mapped_broll(document)
    )
    index = assets.load_index()
    disabled = set(current.disabled)
    suppressed = frozenset(current.suppressed)
    plan = resolver.resolve(
        resolver.ResolveInput(
            words=tuple(words_e), beats=edit_beats, duration=duration, cuts=cuts, passage_breaks=breaks,
            broll_windows=broll_windows, hook_window=studio_hook_window(document, duration),
            sfx_variants=assets.sfx_variants(), beds=index.beds, bed_lengths=index.bed_lengths,
            reactions_available=fx_reactions.reactions_available(),
            user_cues=tuple(mapped_cues(segments, [c for c in user_cues if c.kind not in disabled])),
            suppressed=suppressed,
        ),
        style, template, seed,
    )
    # Slow baseline pushes only where the references use them: style-only and subtle (spec §3.5).
    if not layout_has_zoompan and style.intensity in ("off", "subtle") and (style.zooms or not style.active):
        plan = baseline.baseline_pushes(plan, cuts=cuts, broll_windows=broll_windows)

    user_ids = {c.id for c in user_cues}
    previous_reactions = [c for c in current.cues if c.kind == "reaction" and c.url]
    auto: list[FxCue] = []
    for cue in inverse_mapped_cues(segments, [c for c in plan.cues if c.origin == "auto"]):
        if cue.id in suppressed or cue.id in user_ids:
            continue
        if cue.kind == "reaction" and not cue.url:
            match = next((r for r in previous_reactions if r.query == cue.query), None)
            if match is None:
                continue          # no GIF was ever picked for it here; the next generation attaches one
            previous_reactions.remove(match)
            cue = cue.model_copy(update={"asset_id": match.asset_id, "url": match.url})
        auto.append(cue)
    auto = auto[: max(0, 400 - len(user_cues))]
    fx = current.model_copy(update={
        "style": style, "seed": seed, "mood": plan.mood, "sensitivity": plan.sensitivity, "timing": plan.timing,
        "cues": sorted([*user_cues, *auto], key=lambda c: (c.start, c.id)), "music": plan.music,
        "grade": plan.grade, "grain": plan.grain, "progressBar": plan.progress_bar, "loopEnding": plan.loop_ending,
        "hookTitleSuppressed": plan.hook_title_suppressed,
    })
    resolved = EditDocument.model_validate({**document.model_dump(), "fx": fx.model_dump()})
    return _within_hold_budget(resolved)


def _within_hold_budget(document: EditDocument) -> EditDocument:
    """Drop the latest auto freezes while the draft's freezes hold longer than the budget (a repeated segment plays
    its freeze twice in E, which the resolver cannot see in S)."""
    if document.fx is None:
        return document
    budget = fx_hold_budget(edit_duration(document)) + 1e-6
    cues = list(document.fx.cues)
    while mapped_hold_total(document) > budget:
        freezes = [c for c in cues if c.kind == "freeze" and c.origin == "auto"]
        if not freezes:
            break
        cues.remove(max(freezes, key=lambda c: (c.start, c.id)))
        document = document.model_copy(update={"fx": document.fx.model_copy(update={"cues": list(cues)})})
    return document


def editor_caption_template(style: CaptionStyle) -> dict:
    """The generation template with the editor's own style choices applied on top."""
    from .caption_templates import get_template

    template = dict(get_template(style.template))
    # Templates that emphasise words in their highlight colour follow the editor's choice.
    if (template.get("emphasis_color") or template.get("highlight_color")) == template.get("highlight_color"):
        template["emphasis_color"] = style.accent
    template.update(
        font_color=style.color,
        highlight_color=style.accent,
        uppercase=style.uppercase,
        max_words_per_line=style.wordsPerLine,
    )
    if style.background:
        template["background"] = True
        template["background_color"] = template.get("background_color") or "#00000080"
    else:
        template["background"] = False
        template["background_color"] = None
    if style.karaoke:
        template["animation"] = "karaoke"
    elif template["animation"] == "karaoke":
        template["animation"] = "none"
    return template


def write_ass(
    path: Path,
    document: EditDocument,
    width: int,
    height: int,
    *,
    include_captions: bool = True,
    time_map: Callable[[float], float] | None = None,
    censor_spans: Sequence[tuple[float, float]] | None = None,
    hook_title: bool | str | None = True,
    hook_heavy: bool = False,
):
    """Write captions with the generation renderer so edits keep the clip's animation.

    The keyword arguments are for dynamic-edit renders only (left at their defaults, the file is exactly what
    earlier releases wrote): ``include_captions=False`` leaves the words to fx-core (FX caption templates),
    ``time_map`` moves every event from E to F through the render's time warp, ``censor_spans`` (E) masks
    bleeped words, ``hook_title`` False/None drops the headline (or a string replaces it), and ``hook_heavy``
    gives the headline the Dynamic edits fallback look (heavy outlined type instead of the template's boxes).
    """
    from .media.captions import build_assemblyai_ass_subtitles

    style = document.captions
    show_captions = style.enabled and include_captions
    words = mapped_words(document) if show_captions else []
    caption_transform = document.textTransforms.get("captions", TextTransform())
    hook_transform = document.textTransforms.get("hook", TextTransform())
    original_font_px = max(1, round(style.size * width / 1080))
    font_px = max(1, round(style.size * width * caption_transform.scale / 1080))
    placements = []
    for index in range(0, len(words), style.wordsPerLine):
        group = words[index:index + style.wordsPerLine]
        placement = document.captionTransforms.get(caption_key(document, group))
        placements.append(None if placement is None else {
            "font_px": max(1, round((placement.size if placement.size is not None else style.size) * width * placement.scale / 1080)),
            "x": round((0.5 + placement.x) * width),
            "y": round((style.y + placement.y) * height),
        })
    written = build_assemblyai_ass_subtitles(
        path,
        0,
        sum(s.end - s.start for s in document.segments),
        width,
        height,
        path,
        font_family=style.font,
        caption_words=words,
        include_captions=show_captions,
        template_override=editor_caption_template(style),
        font_px_override=font_px,
        # The editor's position is the caption centre, already kept in range.
        center_y_override=round((style.y + caption_transform.y) * height),
        center_x_override=round((0.5 + caption_transform.x) * width),
        hook_editor_style={
            **hook_transform.model_dump(),
            "base_px": max(34, min(66, int(original_font_px * 0.82))),
            **(document.hookTiming.model_dump() if document.hookTiming else {}),
        },
        # Highlights are per occurrence and already include the generated emphasis.
        emphasis_indices=[i for i, w in enumerate(words) if w["highlight"]],
        wrap_lines=True,
        hook_title=(
            (document.hookTitle.strip() or None) if hook_title is True
            else (hook_title.strip() or None) if isinstance(hook_title, str) else None
        ),
        caption_placements=placements,
        **({"hook_heavy": True} if hook_heavy else {}),
        **({"time_map": time_map} if time_map is not None else {}),
        **({"censor_spans": list(censor_spans)} if censor_spans else {}),
    )
    if not written:
        # Nothing to show (no captions or hook title): keep ffmpeg's ass filter valid.
        path.write_text(
            f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )


def run_render(
    command: list[str],
    directory: Path,
    job_id: str,
    duration: float,
    *,
    should_cancel=None,
    on_progress=None,
):
    """Poll a cancellable child without buffering unbounded ffmpeg logs.

    ``should_cancel`` and ``on_progress`` belong to the job's owner, which
    keeps the job row. The files written here are this process's scratch.
    """
    progress_path = directory / f"progress-{job_id}.txt"
    log_path = directory / f"log-{job_id}.txt"
    with log_path.open("w+") as log:
        # -progress/-nostats are ffmpeg flags. A non-ffmpeg command (tests use
        # sleep) must be started unchanged or GNU sleep rejects the extra args
        # and the cancel path never runs.
        launched = list(command)
        if command and Path(command[0]).name == "ffmpeg":
            launched = command[:1] + ["-progress", str(progress_path), "-nostats"] + command[1:]
        process = subprocess.Popen(
            launched,
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
        try:
            while process.poll() is None:
                if should_cancel is not None and should_cancel():
                    raise InterruptedError("Export cancelled")
                percent = 0
                if progress_path.exists():
                    for line in progress_path.read_text().splitlines():
                        if line.startswith("out_time_us="):
                            try:
                                percent = min(
                                    99,
                                    int(
                                        float(line.split("=", 1)[1])
                                        / 1_000_000
                                        / duration
                                        * 100
                                    ),
                                )
                            except ValueError:
                                pass
                if on_progress is not None:
                    on_progress(percent)
                time.sleep(0.25)
            if process.returncode:
                log.seek(0)
                raise RuntimeError("Video rendering failed: " + log.read()[-1500:])
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            progress_path.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)


class RenderHooks(NamedTuple):
    """``run_render``'s cancel check and progress callback (percent 0-100), owned by the job's row."""

    should_cancel: Callable[[], bool] | None = None
    on_progress: Callable[[int], None] | None = None


@dataclass(frozen=True)
class RenderDocumentContext:
    """What ``render_document`` hands the dynamic-edits renderer (``editor_fx_render.render_document_fx``, §14.1).

    - ``input_path``: the base picture (``layout.mp4`` or ``clean.mp4``), input 0 of P1 and input 1 of P3.
    - ``output_path``: the final ``export-{job_id}.mp4``. Write it through a temporary file and replace, so a
      failed or cancelled render never leaves a partial file under this name.
    - ``head``/``head_label``: the complete P1 picture graph through B-roll: segment cuts of ``[0:v]``, the framing
      geometry, the ``doc.effects`` filters (before the grade, AP-H11) and the B-roll overlays, ending in the
      bracketed ``head_label``. No subtitles; pass it with ``broll_overlays=[]``.
    - ``broll_inputs``: ``-t … -i …`` args of that B-roll (P1 inputs 1..n).
    - ``subtitle_ass_builder(include_captions=, time_map=, censor_spans=, hook_title=)``: writes the draft's ASS
      (hook title, and captions for legacy templates) with the studio's look and returns its path.
    - ``voice_fragment``: the per-segment voice from input 1 ending in ``[fxa_voice0]``, or None (no audio track).
      The draft's volume/mute is not in it: the renderer passes ``voice_gain``.
    - ``encode_args``: the studio's own video encode args (AR-m7).
    - ``segments``: ``(id, s_start, s_end)`` play order, continuous-merged (the E mapping, matte and face track).
    - ``base_video``: ``"layout"`` or ``"clean"`` (a session face track is used only on its own base).
    - ``run_render_hooks``: ``RenderHooks(should_cancel, on_progress)``.

    ``render_document_fx`` returns its render outcome (an object or dict with ``level`` and ``applied_warp``);
    ``render_document`` passes it back to its caller.
    """

    input_path: Path
    output_path: Path
    head: str
    head_label: str
    out_w: int
    out_h: int
    broll_inputs: list[str]
    subtitle_ass_builder: Callable[..., Path]
    fonts_dir: Path | None
    voice_fragment: str | None
    encode_args: list[str]
    segments: list[tuple[str, float, float]]
    base_video: Literal["layout", "clean"]
    session_dir: Path
    run_render_hooks: RenderHooks


def fx_render_outcome(result: Any) -> tuple[str, list[dict]]:
    """``(fx_level, applied_warp)`` from what ``render_document`` returned for a dynamic-edits draft."""
    if isinstance(result, dict):
        level, warp = result.get("level"), result.get("applied_warp")
    else:
        level, warp = getattr(result, "level", None), getattr(result, "applied_warp", None)
    level = level if level in ("full", "safe", "none") else "safe"
    return level, [dict(piece) for piece in (warp or []) if isinstance(piece, dict)]


def _voice_fragment(segments: list[tuple[str, float, float]]) -> str:
    """The draft's voice as P3 hears it: input 1 cut to the segments, joined (no volume; see ``voice_gain``)."""
    if len(segments) == 1:
        _, start, end = segments[0]
        return f"[1:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[fxa_voice0]"
    parts = [
        f"[1:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[fxa_vs{index}]"
        for index, (_, start, end) in enumerate(segments)
    ]
    joins = "".join(f"[fxa_vs{index}]" for index in range(len(segments)))
    return ";".join(parts) + f";{joins}concat=n={len(segments)}:v=0:a=1[fxa_voice0]"


def _fx_render_context(document: EditDocument, *, source: Path, output: Path, ass: Path, fonts: Path | None,
                       overlays: list[dict], geometry: str, effect_filters: str, width: int, height: int,
                       audio: bool, encode_args: list[str], base_video: Literal["layout", "clean"],
                       directory: Path, hooks: RenderHooks) -> RenderDocumentContext:
    """The studio's P1 head (cuts, framing, ``doc.effects``, B-roll; no subtitles) and the rest of the context."""
    from .media.broll_overlay import broll_input_args, broll_overlay_graph

    head = [
        f"[0:v]trim=start={segment.start}:end={segment.end},setpts=PTS-STARTPTS[fxh_v{index}]"
        for index, segment in enumerate(document.segments)
    ]
    head.append(
        "".join(f"[fxh_v{index}]" for index in range(len(document.segments)))
        + f"concat=n={len(document.segments)}:v=1:a=0[fxh_video]"
    )
    head.append(f"[fxh_video]{geometry},{effect_filters}[fxh_framed]")
    head_label = "[fxh_framed]"
    if overlays:
        graph, head_label = broll_overlay_graph("[fxh_framed]", overlays, width, height)
        head.append(graph)

    def subtitle_ass_builder(*, include_captions=True, time_map=None, censor_spans=None, hook_title=True) -> Path:
        write_ass(ass, document, width, height, include_captions=include_captions, time_map=time_map,
                  censor_spans=censor_spans, hook_title=hook_title)
        return ass

    segments = fx_segments(document)
    return RenderDocumentContext(
        input_path=source,
        output_path=output,
        head=";".join(head),
        head_label=head_label,
        out_w=width,
        out_h=height,
        broll_inputs=broll_input_args(overlays),
        subtitle_ass_builder=subtitle_ass_builder,
        fonts_dir=fonts,
        voice_fragment=_voice_fragment(segments) if audio else None,
        encode_args=encode_args,
        segments=segments,
        base_video=base_video,
        session_dir=directory,
        run_render_hooks=hooks,
    )


def render_document(
    directory: Path,
    job_id: str,
    document: EditDocument,
    preset: str,
    broll_files: dict[str, Path] | None = None,
    *,
    should_cancel=None,
    on_progress=None,
):
    """Render a draft. ``broll_files`` maps footage keys to local files; B-roll
    whose footage is missing is left out rather than failing the export.

    A draft with dynamic edits (``document_needs_fx``) renders through
    ``editor_fx_render.render_document_fx`` and returns its outcome; any other
    draft renders exactly as before and returns None."""
    from .clip_editor import _ffprobe_size, _escape_filter_path
    from .media.ffmpeg import audio_fade_out_filter
    from .media.broll_overlay import broll_input_args, broll_overlay_graph, normalize_broll_overlays
    from .video_utils import ass_fonts_dir

    # A split without a source-time jump is one continuous picture. Render it
    # as such so a split inside a dissolve cannot reset its opacity or discard
    # a sub-frame B-roll fragment. Keep the editable draft and its IDs intact.
    document = document.model_copy(update={"segments": continuous_segments(document.segments)})

    base_video = "layout" if document.framing.fit == "auto" and document.framing.aspect == "vertical" and (directory / "layout.mp4").exists() else "clean"
    source = directory / f"{base_video}.mp4"
    sw, sh = _ffprobe_size(source)
    width, height = output_size(document, sw, sh)
    framing, effects = document.framing, document.effects
    scale = (max if framing.fit == "cover" else min)(
        width / sw, height / sh
    ) * framing.zoom
    rw, rh = max(2, round(sw * scale / 2) * 2), max(2, round(sh * scale / 2) * 2)
    # Crop overflow and position letterboxing using the same normalized anchor.
    cw, ch = min(width, rw), min(height, rh)
    geometry = f"scale={rw}:{rh},crop={cw}:{ch}:{(rw - cw) * framing.x}:{(rh - ch) * framing.y},pad={width}:{height}:{(width - cw) * framing.x}:{(height - ch) * framing.y}:black,setsar=1"
    # CSS brightness/contrast use RGB multiplication followed by a midpoint shift.
    gain = effects.brightness / 100 * effects.contrast / 100
    offset = 128 * (1 - effects.contrast / 100)
    effect_filters = f"lutrgb=r='clip(val*{gain}+{offset},0,255)':g='clip(val*{gain}+{offset},0,255)':b='clip(val*{gain}+{offset},0,255)',hue=h={effects.hue}:s={effects.saturation / 100}"
    if effects.blur:
        effect_filters += f",gblur=sigma={effects.blur * width / 1080}"
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    audio = bool(probe.stdout.strip())
    total = sum(s.end - s.start for s in document.segments)
    overlays = normalize_broll_overlays(
        [
            {**piece, "path": broll_files[piece["footage"].key]}
            for piece in mapped_broll(document)
            if broll_files and piece["footage"].key in broll_files
        ],
        total,
        min_duration=BROLL_MIN_SECONDS,
    )
    ass = directory / f"captions-{job_id}.ass"
    fonts = ass_fonts_dir(document.captions.font)
    output = directory / f"export-{job_id}.mp4"
    temporary = directory / f".export-{job_id}.mp4"
    video_encode = [
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-maxrate",
        "12M" if preset == "reels" else "10M",
        "-bufsize",
        "24M",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    # editor_document.render_document, after the B-roll graph is built and before encoding:
    if document_needs_fx(document):          # doc.fx with cues/music/globals, or an FX caption template
        from . import editor_fx_render

        ctx = _fx_render_context(
            document, source=source, output=output, ass=ass, fonts=fonts, overlays=overlays, geometry=geometry,
            effect_filters=effect_filters, width=width, height=height, audio=audio, encode_args=video_encode,
            base_video=base_video, directory=directory, hooks=RenderHooks(should_cancel, on_progress),
        )
        try:
            return editor_fx_render.render_document_fx(document, ctx)   # ctx: RenderDocumentContext (dataclass, WP15a)
        finally:
            ass.unlink(missing_ok=True)
    filters, joins = [], []
    for index, segment in enumerate(document.segments):
        filters.append(
            f"[0:v]trim=start={segment.start}:end={segment.end},setpts=PTS-STARTPTS[v{index}]"
        )
        joins.append(f"[v{index}]")
        if audio:
            filters.append(
                f"[0:a]atrim=start={segment.start}:end={segment.end},asetpts=PTS-STARTPTS[a{index}]"
            )
            joins.append(f"[a{index}]")
    filters.append(
        "".join(joins)
        + f"concat=n={len(document.segments)}:v=1:a={int(audio)}[video]"
        + ("[audio]" if audio else "")
    )
    write_ass(ass, document, width, height)
    subtitles = f"ass={_escape_filter_path(ass)}:fontsdir={_escape_filter_path(fonts or Path('fonts'))}"
    if overlays:
        # Footage fills the output frame over the framed, graded picture and
        # under the captions, as in the preview.
        filters.append(f"[video]{geometry},{effect_filters}[framed]")
        graph, label = broll_overlay_graph("[framed]", overlays, width, height)
        filters.append(graph)
        filters.append(f"{label}{subtitles}[outv]")
    else:
        filters.append(f"[video]{geometry},{effect_filters},{subtitles}[outv]")
    # Mixed source/stock frame rates must not make ffmpeg duplicate tail frames.
    filters.append("[outv]fps=30[finalv]")
    if audio:
        # Segment ends are wherever the user cut; the export's last moment
        # fades so it cannot click.
        filters.append(
            f"[audio]volume={0 if document.muted else document.volume},"
            f"{audio_fade_out_filter(total)}[outa]"
        )
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        *broll_input_args(overlays),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[finalv]",
    ]
    if audio:
        command += ["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"]
    command += [
        *video_encode,
        "-t",
        f"{total:.6f}",
        str(temporary),
    ]
    try:
        run_render(
            command,
            directory,
            job_id,
            sum(s.end - s.start for s in document.segments),
            should_cancel=should_cancel,
            on_progress=on_progress,
        )
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Export cancelled")
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        ass.unlink(missing_ok=True)
