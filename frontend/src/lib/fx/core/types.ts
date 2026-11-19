// Mirror of backend/src/edit_fx/contracts.py. Keep names, literals and defaults identical.
export const EDIT_FX_VERSION = "fx-v2";
export const FX_SCENE_VERSION = 1;
export const FPS = 30;
export const FRAME = 1 / FPS;

export type Intensity = "off" | "subtle" | "bold" | "max";
export const INTENSITIES: readonly Intensity[] = ["off", "subtle", "bold", "max"];
export type Mood = "hype" | "chill" | "dramatic" | "funny" | "inspiring" | "dark" | "aggressive";
export type GradeId = "none" | "punchy" | "warm" | "teal_orange" | "noir" | "vhs" | "dream";
export type Censor = "off" | "bleep" | "bleep_bar" | "bleep_pixelate";
export type BeatKind = "hook" | "emphasis" | "number" | "punchline" | "swear" | "emotional_turn" | "payoff" | "build";
export type Tone = "neutral" | "hype" | "funny" | "shocked" | "sad" | "angry" | "wholesome" | "suspense" | "awkward" | "smug";
export type Sensitivity = "normal" | "serious";
export type Timing = "word" | "approx" | "none";
export type SoundId = "whoosh" | "impact" | "pop" | "riser" | "record_scratch" | "boom" | "cash_register"
  | "ticker" | "slap" | "stamp" | "glitch" | "tape_stop";
export type TransitionStyle = "whip" | "flash" | "glitch" | "zoom_through" | "spin" | "light_leak";
export type AccentVariant = "circle" | "underline" | "strike" | "arrow" | "highlight" | "emoji" | "emoji_burst"
  | "slam" | "behind" | "counter" | "decode" | "kinetic" | "doodle";
export type DoodleKind = "sparkles" | "arrows" | "sweat" | "horns" | "bang" | "heart" | "anger";
export type FxKind = "accent" | "zoom" | "shake" | "flash" | "glitch" | "desaturate" | "transition"
  | "sfx" | "bleep" | "speed" | "freeze" | "sticker" | "reaction";
export const INSTANT_KINDS: ReadonlySet<FxKind> = new Set(["sfx", "freeze", "transition"]);
export const FX_CAPTION_TEMPLATES = ["marker_circle", "emoji_drop", "slam", "behind_head", "stat_counter",
  "kinetic_stack", "decode_glitch", "highlighter_swipe", "face_doodles"] as const;
export type FxCaptionTemplate = (typeof FX_CAPTION_TEMPLATES)[number];
export const isFxTemplate = (id: string | null | undefined): id is FxCaptionTemplate =>
  !!id && (FX_CAPTION_TEMPLATES as readonly string[]).includes(id);
/** The animated styles offered in catalogues and pickers; the others stay valid (stored tasks and drafts) but unlisted. */
export const LISTED_FX_CAPTION_TEMPLATES = ["highlighter_swipe", "emoji_drop", "behind_head"] as const;
export const isListedFxTemplate = (id: string | null | undefined): boolean =>
  !!id && (LISTED_FX_CAPTION_TEMPLATES as readonly string[]).includes(id);
export type CaptionLook = "template" | "subtle" | "bold" | "max";
/** == contracts.caption_look_for: listed templates follow the intensity (off = Bold look, serious = Subtle). */
export function captionLookFor(template: string | null | undefined, intensity: string | null | undefined,
  sensitivity: string = "normal"): CaptionLook {
  if (!isListedFxTemplate(template)) return "template";
  if (sensitivity === "serious") return "subtle";
  return intensity === "subtle" || intensity === "bold" || intensity === "max" ? intensity : "bold";
}
/** Flash: 3 frames (one before the cut, the cut, one after); light leak: 6 frames. */
export const TRANSITION_HALF: Record<TransitionStyle, number> = {
  whip: 0.12, flash: 0.05, glitch: 0.1, zoom_through: 0.18, spin: 0.2, light_leak: 0.1,
};
/** Transition styles the resolver places on its own; the rest only by hand in the studio. */
export const AUTO_TRANSITION_STYLES: readonly TransitionStyle[] = ["flash", "light_leak"];

export interface EditStyle {
  intensity: Intensity; sfx: boolean; music: boolean; music_mood: "auto" | Mood; zooms: boolean; shake: boolean;
  transitions: boolean; speed: boolean; censor: Censor; grade: "auto" | GradeId; grain: boolean;
  progress_bar: boolean; loop_ending: boolean; wait_for_it: boolean; reactions: boolean;
}
export const DEFAULT_EDIT_STYLE: EditStyle = {
  intensity: "off", sfx: true, music: true, music_mood: "auto", zooms: true, shake: true, transitions: true,
  speed: true, censor: "bleep_bar", grade: "auto", grain: false, progress_bar: false, loop_ending: false,
  wait_for_it: true, reactions: false,
};
/** Mirror of EditStyle.to_json(): {} when off; sorted non-default keys with intensity. */
export function editStyleToJson(style: EditStyle): Partial<EditStyle> {
  if (style.intensity === "off") return {};
  const out: Record<string, unknown> = {};
  const defaults = DEFAULT_EDIT_STYLE as unknown as Record<string, unknown>;
  const values = style as unknown as Record<string, unknown>;
  // Same order as Python's sorted(dumped): code-point order of the keys, only the declared fields.
  const keys = Object.keys(DEFAULT_EDIT_STYLE).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  for (const key of keys) {
    if (key === "intensity" || values[key] !== defaults[key]) out[key] = values[key];
  }
  return out as Partial<EditStyle>;
}

export interface Beat {
  id: string; kind: BeatKind; start: number; end: number; text: string; strength: 1 | 2 | 3; tone: Tone;
  emoji: string; label: string; value: number | null; prefix: string; suffix: string;
  source: "llm" | "heuristic" | "user"; src_start: number | null; src_end: number | null; words: string[];
}
export interface EditBeats {
  version: "beats-v1"; words_hash: string; timeline_hash: string; model: string; source: "llm" | "heuristic"; mood: Mood;
  sensitivity: Sensitivity; timing: Timing; hook_end: number | null; beats: Beat[];
  reactions: { at: number; query: string }[];
}

interface CueBase { id: string; start: number; end: number; seed: number; beat: string; origin: "auto" | "user" }
export type AccentCue = CueBase & { kind: "accent"; variant: AccentVariant; text: string; emoji: string;
  doodle: DoodleKind; value: number | null; prefix: string; suffix: string };
export type ZoomCue = CueBase & { kind: "zoom"; mode: "punch" | "push" | "pull" | "nudge"; scale: number; anchor: "face" | "center" };
export type ShakeCue = CueBase & { kind: "shake"; amp: number; freq: number };
export type FlashCue = CueBase & { kind: "flash"; color: string; peak: number };
export type GlitchCue = CueBase & { kind: "glitch"; amount: number };
export type DesaturateCue = CueBase & { kind: "desaturate"; amount: number; dim: number };
export type TransitionCue = CueBase & { kind: "transition"; style: TransitionStyle };
export type SfxCue = CueBase & { kind: "sfx"; sound: SoundId; variant: number; gain_db: number };
export type BleepCue = CueBase & { kind: "bleep"; tone: boolean; cover: "none" | "bar" | "pixelate" };
export type SpeedCue = CueBase & { kind: "speed"; rate: number };
export type FreezeCue = CueBase & { kind: "freeze"; hold: number; label: string };
export type StickerCue = CueBase & { kind: "sticker"; sticker: "wait_for_it" | "text"; text: string };
export type ReactionCue = CueBase & { kind: "reaction"; query: string; provider: "giphy"; asset_id: string; url: string };
export type FxCue = AccentCue | ZoomCue | ShakeCue | FlashCue | GlitchCue | DesaturateCue | TransitionCue
  | SfxCue | BleepCue | SpeedCue | FreezeCue | StickerCue | ReactionCue;
export type CueOf<K extends FxKind> = Extract<FxCue, { kind: K }>;

export interface MusicBed {
  mood: Mood; bed: string; gain_db: number; duck_db: number; attack: number; release: number; offset: number;
  fade_in: number; fade_out: number;
}
export interface FxPlan {
  version: 1; resolver: string; style: EditStyle; template: string; seed: number; edit_duration: number;
  sensitivity: Sensitivity; timing: Timing; mood: Mood | null; cues: FxCue[]; music: MusicBed | null;
  grade: GradeId; grain: number; progress_bar: boolean; loop_ending: "none" | "crossfade";
  hook_title_suppressed: boolean;
}
export interface FaceTrack { version: 1; space: "picture" | "output"; fps: number; samples: [number, number[][]][] }
export interface CaptionPlacement { key: string; x: number; y: number; scale: number }
/** Caption centre `y` for one stretch of the clip (an interview shot), E seconds (== contracts.CaptionRow). A change of
 *  `y` is a camera cut, which ends the caption group on screen (captions/groups.ts phraseGroups `cuts`). */
export interface CaptionRow { start: number; end: number; y: number }
/** == caption_rows.row_cuts: the camera cuts that end caption groups, where the row height changes. */
export function rowCuts(rows: readonly CaptionRow[] | undefined): number[] {
  const ordered = [...(rows ?? [])].sort((a, b) => a.start - b.start);
  const cuts: number[] = [];
  for (let i = 1; i < ordered.length; i++) if (ordered[i].y !== ordered[i - 1].y) cuts.push(ordered[i].start);
  return cuts;
}
/** == contracts.caption_row_y: rows (sorted, not overlapping) as a step function; `defaultY` without rows. */
export function captionRowY(rows: readonly CaptionRow[] | undefined, defaultY: number, t: number): number {
  if (!rows || !rows.length) return defaultY;
  let pick = rows[0];
  for (const row of rows) {
    if (row.start > t) break;
    pick = row;
  }
  return pick.y;
}
export interface FxCaptionStyle {
  template: string; font: string; size_px: number; color: string; accent: string; stroke_color: string | null;
  stroke_px: number; shadow: boolean; background: string | null; y: number;
  /** Caption centre offset from the middle as a fraction of width (studio global transform); absent = 0. */
  x?: number; words_per_line: number;
  uppercase: boolean; enabled: boolean; placements: CaptionPlacement[];
  /** "template" (absent): the template's own motion; subtle/bold/max: the intensity looks (captions/looks.ts). */
  look?: CaptionLook;
  /** Per-shot rows (interview layouts): a row change is a camera cut that ends the group on screen; a group without a
   *  placement sits on the row where it starts showing (its first word, or the cut it opens at). Absent or empty:
   *  every group at `y`. */
  rows?: CaptionRow[];
}
/** The hook title drawn by fx-core (== contracts.FxHookTitle); see hook.ts. */
export interface FxHookTitle {
  /** As shown (casing already applied). */
  text: string;
  /** Bundled font stem. */
  font: string;
  /** Marker colour behind numbers and power words, "#RRGGBB". */
  accent: string;
  /** Size, tilt and entrance flavour. */
  look: "subtle" | "bold" | "max";
  /** The clip's caption template: picks the title's style (hook.ts HOOK_VARIANTS). Absent or unknown: the stack. */
  template?: string;
  /** E window. */
  start: number; end: number;
  /** Studio transform: offset from the automatic position (fractions of W and H) and scale. */
  x: number; y: number; scale: number;
}
export interface FxWord { id: string; start: number; end: number; text: string; highlight: boolean; run: string }
export interface WarpPieceJson { e0: number; e1: number; o0: number; rate: number; hold: number }
export interface MatteWindow { start: number; end: number; person: boolean }
export interface FxScene {
  version: 1; width: number; height: number; edit_duration: number; out_duration: number; warp: WarpPieceJson[];
  template: string; cues: FxCue[]; words: FxWord[]; caption: FxCaptionStyle | null; grade: GradeId; grain: number;
  progress_bar: boolean; progress_color: string; loop_ending: "none" | "crossfade";
  hook_window: [number, number] | null; hook_title_suppressed: boolean;
  /** The hook title fx-core draws; null or absent: none, or the ASS layer burns it. */
  hook?: FxHookTitle | null;
  face_track: FaceTrack | null;
  broll_windows: [number, number][]; matte_windows: MatteWindow[]; seed: number;
}
