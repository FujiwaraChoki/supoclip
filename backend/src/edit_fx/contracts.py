"""Dynamic edits contracts (fx-v1).

Python decides WHAT happens WHEN (beats, resolver, warp, audio); fx-core (TypeScript) decides HOW IT LOOKS.
Pure data: no I/O, no ffmpeg/cv2 imports. Safe to import from the API, the video worker and the AI worker.
Mirrored by frontend/src/lib/fx/core/types.ts: keep field names, literals and defaults identical.
Clocks: S = clip/source seconds, E = edit seconds (== S in generation), F = final seconds (after the warp).
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

EDIT_FX_VERSION = "fx-v2"     # bump when resolver output changes for the same inputs
BEATS_VERSION = "beats-v3"    # bump when the beat prompt, schema or validation changes (the cache key; EditBeats.version
                              # stays the stored schema version "beats-v1")
FX_SCENE_VERSION = 1          # bump when FxScene (the P2 / browser contract) changes shape
FPS = 30
FRAME = 1.0 / FPS

Intensity = Literal["off", "subtle", "bold", "max"]
INTENSITIES: tuple[str, ...] = ("off", "subtle", "bold", "max")
Mood = Literal["hype", "chill", "dramatic", "funny", "inspiring", "dark", "aggressive"]
MOODS: tuple[str, ...] = ("hype", "chill", "dramatic", "funny", "inspiring", "dark", "aggressive")
GradeId = Literal["none", "punchy", "warm", "teal_orange", "noir", "vhs", "dream"]
GRADES: tuple[str, ...] = ("none", "punchy", "warm", "teal_orange", "noir", "vhs", "dream")
Censor = Literal["off", "bleep", "bleep_bar", "bleep_pixelate"]
BeatKind = Literal["hook", "emphasis", "number", "punchline", "swear", "emotional_turn", "payoff", "build"]
Tone = Literal["neutral", "hype", "funny", "shocked", "sad", "angry", "wholesome", "suspense", "awkward", "smug"]
Sensitivity = Literal["normal", "serious"]
Timing = Literal["word", "approx", "none"]
SoundId = Literal["whoosh", "impact", "pop", "riser", "record_scratch", "boom", "cash_register",
                  "ticker", "slap", "stamp", "glitch", "tape_stop"]
SOUND_IDS: tuple[str, ...] = ("whoosh", "impact", "pop", "riser", "record_scratch", "boom", "cash_register",
                              "ticker", "slap", "stamp", "glitch", "tape_stop")
TransitionStyle = Literal["whip", "flash", "glitch", "zoom_through", "spin", "light_leak"]
AccentVariant = Literal["circle", "underline", "strike", "arrow", "highlight", "emoji", "emoji_burst",
                        "slam", "behind", "counter", "decode", "kinetic", "doodle"]
DoodleKind = Literal["sparkles", "arrows", "sweat", "horns", "bang", "heart", "anger"]
FxKind = Literal["accent", "zoom", "shake", "flash", "glitch", "desaturate", "transition",
                 "sfx", "bleep", "speed", "freeze", "sticker", "reaction"]
FX_KINDS: tuple[str, ...] = ("accent", "zoom", "shake", "flash", "glitch", "desaturate", "transition",
                             "sfx", "bleep", "speed", "freeze", "sticker", "reaction")
INSTANT_KINDS: frozenset[str] = frozenset({"sfx", "freeze", "transition"})   # start == end; mapped by start only

FX_CAPTION_TEMPLATES: tuple[str, ...] = (
    "marker_circle", "emoji_drop", "slam", "behind_head", "stat_counter",
    "kinetic_stack", "decode_glitch", "highlighter_swipe", "face_doodles",
)
LEGACY_CAPTION_TEMPLATES: tuple[str, ...] = ("default", "hormozi", "mrbeast", "minimal", "tiktok", "neon", "podcast")
# The animated styles offered in catalogues and pickers. The others stay valid ids (stored tasks, drafts and API
# callers keep rendering them with their own drawers) but are never listed.
LISTED_FX_CAPTION_TEMPLATES: tuple[str, ...] = ("highlighter_swipe", "emoji_drop", "behind_head")
# Templates drawn with the intensity caption looks (CAPTION_LOOKS): phrase groups, word-by-word reveal, keyword boxes.
LOOK_TEMPLATES: frozenset[str] = frozenset(LISTED_FX_CAPTION_TEMPLATES)
CaptionLook = Literal["template", "subtle", "bold", "max"]


def is_fx_template(template_id: str | None) -> bool:
    return template_id in FX_CAPTION_TEMPLATES


def is_listed_fx_template(template_id: str | None) -> bool:
    return template_id in LISTED_FX_CAPTION_TEMPLATES


def caption_look_for(template_id: str | None, intensity: str | None, sensitivity: str = "normal") -> str:
    """The caption look of a template at an intensity: hidden templates keep their own ("template"); listed ones
    follow the intensity, style-only (off) draws the Bold look, serious clips the Subtle one."""
    if template_id not in LOOK_TEMPLATES:
        return "template"
    if sensitivity == "serious":
        return "subtle"
    return intensity if intensity in ("subtle", "bold", "max") else "bold"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


# ----------------------------------------------------------------------------- user choice

class EditStyle(_Strict):
    """What the user picked. Stored in tasks.edit_style as to_json() (non-default keys only); NULL/{} == off.
    An FX caption template with intensity off is "style only": captions are drawn by fx-core, nothing else changes."""
    intensity: Intensity = "off"
    sfx: bool = True
    music: bool = True
    music_mood: Literal["auto", "hype", "chill", "dramatic", "funny", "inspiring", "dark", "aggressive"] = "auto"
    zooms: bool = True
    shake: bool = True
    transitions: bool = True
    speed: bool = True               # speed ramps + freeze frames (time warp); also needs FX_WARP=on
    censor: Censor = "bleep_bar"
    grade: Literal["auto", "none", "punchy", "warm", "teal_orange", "noir", "vhs", "dream"] = "auto"
    grain: bool = False
    progress_bar: bool = False
    loop_ending: bool = False
    wait_for_it: bool = True
    reactions: bool = False          # GIF/meme inserts: opt-in, needs REACTIONS_PROVIDER + key

    @property
    def active(self) -> bool:
        return self.intensity != "off"

    def to_json(self) -> dict:
        """Canonical: sorted, only non-default keys, `intensity` always present when active. {} when off."""
        if not self.active:
            return {}
        defaults = EditStyle().model_dump()
        dumped = self.model_dump()
        return {k: dumped[k] for k in sorted(dumped) if k == "intensity" or dumped[k] != defaults[k]}

    def fingerprint(self) -> str:
        blob = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ----------------------------------------------------------------------------- beats (model picks)

class Beat(_Strict):
    """One tagged moment, S seconds (word-exact, derived from word times by code, never by the model)."""
    id: str = Field(min_length=1, max_length=24)        # "b0".. in time order at planning time; "u<n>" user-made
    kind: BeatKind
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(default="", max_length=200)
    strength: int = Field(default=2, ge=1, le=3)
    tone: Tone = "neutral"
    emoji: str = Field(default="", max_length=16)        # an emoji atlas key (the emoji itself) or ""
    label: str = Field(default="", max_length=40)        # sticker / freeze label text or ""
    value: Optional[float] = None                        # parsed by code for number beats
    prefix: str = Field(default="", max_length=4)        # "$", "€", "#"
    suffix: str = Field(default="", max_length=8)        # "%", "M", "k", "x"
    source: Literal["llm", "heuristic", "user"] = "llm"
    src_start: Optional[float] = None                    # A seconds (source video) of the first word; re-anchoring (§13.1)
    src_end: Optional[float] = None                      # A seconds of the last word
    words: tuple[str, ...] = Field(default=(), max_length=8)   # normalised word texts, re-anchoring identity check

    @model_validator(mode="after")
    def _ordered(self):
        if self.end < self.start:
            raise ValueError("beat end before start")
        return self


class ReactionIdea(_Strict):
    at: float = Field(ge=0)                              # S seconds of the word it reacts to
    query: str = Field(min_length=1, max_length=40)


class EditBeats(_Strict):
    """Sidecar key `edit_beats` and document `fx.beats` source. Intensity-independent."""
    version: Literal["beats-v1"] = "beats-v1"
    words_hash: str = Field(max_length=64)
    timeline_hash: str = Field(default="", max_length=64)   # sha256(ranges + offsets + words_hash), §13.1
    model: str = Field(default="", max_length=80)        # "provider:model" for llm, "" for heuristic
    source: Literal["llm", "heuristic"] = "llm"
    mood: Mood = "hype"
    sensitivity: Sensitivity = "normal"
    timing: Timing = "word"
    hook_end: Optional[float] = None                     # S end of the opening hook, None when none
    beats: tuple[Beat, ...] = Field(default=(), max_length=200)
    reactions: tuple[ReactionIdea, ...] = Field(default=(), max_length=3)


# ----------------------------------------------------------------------------- cues (code decides; editable)

class CueBase(_Strict):
    id: str = Field(min_length=1, max_length=32)          # "<beat>.<purpose>" | "g.<purpose>" | "kb<n>" | "u<n>"; "@k" suffix after mapping
    start: float = Field(ge=0)                            # S in sidecar/document, E in FxScene
    end: float = Field(ge=0)
    seed: int = Field(default=0, ge=0, le=0xFFFFFFFF)
    beat: str = Field(default="", max_length=24)          # producing beat id, "" for global/user
    origin: Literal["auto", "user"] = "auto"              # re-resolve replaces auto cues only

    @model_validator(mode="after")
    def _ordered(self):
        if self.end < self.start:
            raise ValueError("cue end before start")
        if getattr(self, "kind", None) in INSTANT_KINDS and self.end != self.start:
            raise ValueError("instant cue must have start == end")
        return self


class AccentCue(CueBase):
    kind: Literal["accent"] = "accent"
    variant: AccentVariant
    text: str = Field(default="", max_length=120)          # the word(s) as displayed (censored if needed)
    emoji: str = Field(default="", max_length=16)          # emoji / emoji_burst atlas key
    doodle: DoodleKind = "sparkles"
    value: Optional[float] = None                          # counter target
    prefix: str = Field(default="", max_length=4)
    suffix: str = Field(default="", max_length=8)


class ZoomCue(CueBase):
    kind: Literal["zoom"] = "zoom"
    mode: Literal["punch", "push", "pull", "nudge"]        # punch = hard reframe held to `end`; push/pull = eased ramp
                                                           # to/from `scale`; nudge = 11-frame eased push, then held
    scale: float = Field(ge=1.0, le=1.6)
    anchor: Literal["face", "center"] = "face"             # face falls back to centre (0.5, 0.38) when no face at t


class ShakeCue(CueBase):
    kind: Literal["shake"] = "shake"
    amp: float = Field(ge=0, le=40)                        # px at 1080 wide
    freq: float = Field(default=18, ge=4, le=30)


class FlashCue(CueBase):
    kind: Literal["flash"] = "flash"
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")
    peak: float = Field(default=0.85, ge=0, le=1)


class GlitchCue(CueBase):
    kind: Literal["glitch"] = "glitch"
    amount: float = Field(ge=0, le=1)                      # slice displacement + RGB split strength


class DesaturateCue(CueBase):
    kind: Literal["desaturate"] = "desaturate"
    amount: float = Field(ge=0, le=1)
    dim: float = Field(default=0.0, ge=0, le=0.8)


class TransitionCue(CueBase):
    kind: Literal["transition"] = "transition"            # instant at the cut; visual window from TRANSITION_HALF
    style: TransitionStyle


class SfxCue(CueBase):
    kind: Literal["sfx"] = "sfx"                           # instant: the sound starts at `start`
    sound: SoundId
    variant: int = Field(default=0, ge=0, le=15)
    gain_db: float = Field(default=0, ge=-30, le=6)


class BleepCue(CueBase):
    kind: Literal["bleep"] = "bleep"
    tone: bool = True                                      # False = mute only
    cover: Literal["none", "bar", "pixelate"] = "bar"      # drawn only if mouth landmarks exist at t


class SpeedCue(CueBase):
    kind: Literal["speed"] = "speed"
    rate: float = Field(ge=0.25, le=4.0)                   # edit seconds per final second (2.0 = twice as fast)


class FreezeCue(CueBase):
    kind: Literal["freeze"] = "freeze"                     # instant: the frozen E instant
    hold: float = Field(ge=0.2, le=2.5)
    label: str = Field(default="", max_length=40)


class StickerCue(CueBase):
    kind: Literal["sticker"] = "sticker"
    sticker: Literal["wait_for_it", "text"]
    text: str = Field(default="", max_length=40)


class ReactionCue(CueBase):
    kind: Literal["reaction"] = "reaction"
    query: str = Field(max_length=60)
    provider: Literal["giphy"] = "giphy"
    asset_id: str = Field(default="", max_length=64)      # provider id; resolved to a file per machine
    url: str = Field(default="", max_length=600)          # provider mp4 rendition URL (never a local path)


FxCue = Annotated[
    Union[AccentCue, ZoomCue, ShakeCue, FlashCue, GlitchCue, DesaturateCue, TransitionCue,
          SfxCue, BleepCue, SpeedCue, FreezeCue, StickerCue, ReactionCue],
    Field(discriminator="kind"),
]

TRANSITION_HALF: dict[str, float] = {      # visual half-window (s) around the cut, mirrored in fx-core
    # flash: 3 frames (one before the cut, the cut, one after); light_leak: 6 frames (3 before, 3 after)
    "whip": 0.12, "flash": 0.05, "glitch": 0.10, "zoom_through": 0.18, "spin": 0.20, "light_leak": 0.10,
}
# Transition styles the resolver places on its own; the rest are only placed by hand in the studio.
AUTO_TRANSITION_STYLES: tuple[str, ...] = ("flash", "light_leak")


class MusicBed(_Strict):
    mood: Mood
    bed: str = Field(min_length=1, max_length=80)          # asset id, e.g. "hype-tent" or a drop-in "hype-mytrack"
    gain_db: float = Field(default=-20, ge=-40, le=12)     # bed level with no speech (0 dB = the bed at -20 LUFS)
    duck_db: float = Field(default=-12, ge=-30, le=0)      # extra attenuation under speech
    attack: float = Field(default=0.08, ge=0.01, le=1)
    release: float = Field(default=0.35, ge=0.05, le=2)
    offset: float = Field(default=0, ge=0)                 # seconds into the bed at F = 0
    fade_in: float = Field(default=0.5, ge=0, le=3)
    fade_out: float = Field(default=0.8, ge=0, le=3)


class FxPlan(_Strict):
    """Resolved plan for one clip. Sidecar key `edit_fx`. Cue times are S (== E for generation)."""
    version: Literal[1] = 1
    resolver: str = EDIT_FX_VERSION
    style: EditStyle
    template: str = Field(max_length=50)
    seed: int = Field(ge=0, le=0xFFFFFFFF)
    edit_duration: float = Field(ge=0)
    sensitivity: Sensitivity = "normal"
    timing: Timing = "word"
    mood: Optional[Mood] = None
    cues: tuple[FxCue, ...] = Field(default=(), max_length=400)
    music: Optional[MusicBed] = None
    grade: GradeId = "none"
    grain: float = Field(default=0, ge=0, le=1)
    progress_bar: bool = False
    loop_ending: Literal["none", "crossfade"] = "none"
    hook_title_suppressed: bool = False

    @property
    def draws(self) -> bool:
        """True when the deno stage (P2) is needed."""
        return (is_fx_template(self.template) or self.grade != "none" or self.grain > 0 or self.progress_bar
                or self.loop_ending != "none"
                or any(c.kind not in ("sfx", "bleep") or (c.kind == "bleep" and c.cover != "none") for c in self.cues))

    @property
    def needs_face(self) -> bool:
        return any((c.kind == "zoom" and c.anchor == "face") or (c.kind == "accent" and c.variant == "doodle")
                   or (c.kind == "bleep" and c.cover != "none") for c in self.cues)

    @property
    def needs_matte(self) -> bool:
        return any(c.kind == "accent" and c.variant == "behind" for c in self.cues)

    @property
    def has_warp(self) -> bool:
        return any(c.kind in ("speed", "freeze") for c in self.cues)

    @property
    def has_audio(self) -> bool:
        return self.music is not None or any(c.kind in ("sfx", "bleep") for c in self.cues)


# ----------------------------------------------------------------------------- per-render inputs

class FaceTrack(_Strict):
    """Normalised (0-1) face boxes at `fps`. space "picture" = relative to the base picture (generation: the framed
    output frame; studio: layout.mp4 or clean.mp4 frame). Each face row is [x,y,w,h,score] or
    [x,y,w,h,score, rex,rey, lex,ley, nx,ny, rmx,rmy, lmx,lmy] (YuNet landmark order). Largest face first, ≤ 2 faces."""
    version: Literal[1] = 1
    space: Literal["picture", "output"] = "picture"
    fps: float = Field(default=6, gt=0, le=30)
    samples: tuple[tuple[float, tuple[tuple[float, ...], ...]], ...] = ()


class CaptionPlacement(_Strict):
    key: str = Field(max_length=200)        # caption_key: JSON.stringify([run, firstWordId])
    x: float
    y: float
    scale: float = Field(default=1, gt=0, le=4)


class CaptionRow(_Strict):
    """Where captions sit during one stretch of the clip (an interview shot): the caption centre ``y`` for every
    caption group shown from ``[start, end)``. A change of ``y`` is a camera cut, and a cut ends the caption group on
    screen (``caption_groups.phrase_groups(cuts=...)``). E seconds (S in a studio document's ``captionRows``)."""
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    y: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self):
        if self.end < self.start:
            raise ValueError("caption row end before start")
        return self


def caption_row_y(rows, default_y: float, t: float) -> float:
    """== fx-core ``captionRowY``: rows (sorted by start, not overlapping) as a step function: the ``y`` of the last
    row starting at or before ``t`` (the first row before it), ``default_y`` when there are none. A time past the
    last row, or in a gap, keeps the row before it."""
    if not rows:
        return default_y
    pick = rows[0]
    for row in rows:
        if row.start > t:
            break
        pick = row
    return pick.y


class FxCaptionStyle(_Strict):
    """Resolved look for FX-template captions (drawn by fx-core, never by ASS)."""
    template: str = Field(max_length=50)
    font: str = Field(max_length=80)        # bundled font stem, e.g. "TikTokSans-Bold" (file in backend/fonts/)
    size_px: float = Field(gt=0, le=400)    # at output width
    color: str
    accent: str
    stroke_color: Optional[str] = None
    stroke_px: float = Field(default=0, ge=0, le=40)
    shadow: bool = False
    background: Optional[str] = None        # "#RRGGBBAA" line box or None
    y: float = Field(ge=0, le=1)            # caption centre as a fraction of height
    x: float = Field(default=0, ge=-1, le=1)   # caption centre offset from the middle, fraction of width (studio)
    words_per_line: int = Field(default=3, ge=1, le=8)
    uppercase: bool = False
    enabled: bool = True
    placements: tuple[CaptionPlacement, ...] = ()
    # "template": the template's own motion (fixed word groups, karaoke, entrances). subtle/bold/max: the intensity
    # looks measured from reference edits (phrase groups of up to words_per_line, word-by-word reveal at bold/max,
    # 0-frame entrances, a 2-frame pop at max, keyword boxes), see CAPTION_LOOKS in fx-core captions/looks.ts.
    look: CaptionLook = "template"
    # Per-shot caption rows (interview layouts): a row change is a camera cut, which ends the caption group on screen;
    # each look group without a placement sits on the row in effect where it starts showing (its first word, or the
    # cut it opens at), so captions switch exactly at cuts and never move inside a group. Empty: every caption sits at
    # ``y``. Placements are absolute (``y`` + offset) and ignore rows.
    rows: tuple[CaptionRow, ...] = Field(default=(), max_length=400)


class FxHookTitle(_Strict):
    """The hook title drawn by fx-core (``hook.ts``): a white label hugging each line or big outlined type, by the
    clip's caption template, with the numbers and power words marked. fx-core lays it out and places it; see
    ``edit_fx/hook_title.py``."""
    text: str = Field(min_length=1, max_length=200)   # as shown (casing already applied)
    font: str = Field(max_length=80)        # bundled font stem
    accent: str = "#FFE45C"                 # marker colour, "#RRGGBB"
    look: Literal["subtle", "bold", "max"] = "bold"
    template: str = Field(default="", max_length=40)   # the caption template: picks the title's style (HOOK_VARIANTS)
    start: float = Field(ge=0)              # E window
    end: float = Field(ge=0)
    x: float = Field(default=0, ge=-1, le=1)   # studio offset from the automatic position (fractions of W and H)
    y: float = Field(default=0, ge=-1, le=1)
    scale: float = Field(default=1, ge=0.2, le=4)


class FxWord(_Strict):
    id: str = Field(max_length=40)
    start: float
    end: float
    text: str = Field(max_length=120)       # already censored where a bleep covers it
    highlight: bool = False
    run: str = Field(default="clip", max_length=40)   # continuous-run segment id (studio caption_key), "clip" in generation


class WarpPieceJson(_Strict):
    e0: float                               # edit start
    e1: float                               # edit end (== e0 for a hold)
    o0: float                               # final start
    rate: float                             # edit s per final s; 0 for a hold
    hold: float = 0.0                       # final seconds of a hold piece


class MatteWindow(_Strict):
    start: float
    end: float
    person: bool                            # False → behind accent draws in front


class FxScene(_Strict):
    """Everything P2 (deno) or the browser needs to draw one render. E time unless stated."""
    version: Literal[1] = 1
    width: int
    height: int
    edit_duration: float
    out_duration: float                     # F
    warp: tuple[WarpPieceJson, ...] = ()    # empty = identity
    template: str
    cues: tuple[FxCue, ...] = ()            # E time, enabled kinds only, sorted by (start, id)
    words: tuple[FxWord, ...] = ()
    caption: Optional[FxCaptionStyle] = None   # None: captions drawn by ASS (legacy template) or disabled
    grade: GradeId = "none"
    grain: float = 0
    progress_bar: bool = False
    progress_color: str = "#FF7A1A"
    loop_ending: Literal["none", "crossfade"] = "none"
    hook_window: Optional[tuple[float, float]] = None
    hook_title_suppressed: bool = False
    hook: Optional[FxHookTitle] = None      # the title fx-core draws; None: no title, or the ASS layer burns it
    face_track: Optional[FaceTrack] = None  # space "output" (picture_rect applied), samples on the E clock (studio:
                                            # resampled through locate(e) at 6 fps by build_scene / studioScene, AR-B4)
    broll_windows: tuple[tuple[float, float], ...] = ()
    matte_windows: tuple[MatteWindow, ...] = ()
    seed: int = 0


# ----------------------------------------------------------------------------- LLM output schema
# Required fields only, no numeric constraints (grammar-size rule); code clamps. Word ids, never timestamps.

class BeatTagLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: BeatKind
    first_word: int
    last_word: int
    strength: int
    tone: Tone
    emoji: str
    label: str


class ReactionLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")
    word: int
    query: str


class EditPlanLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mood: Mood
    sensitivity: Sensitivity
    hook_last_word: int
    beats: list[BeatTagLLM]
    reactions: list[ReactionLLM]
