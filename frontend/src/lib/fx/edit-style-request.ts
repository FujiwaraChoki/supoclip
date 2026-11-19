// Dynamic edits on the request side: what `/tasks/edit-styles` offers, the stored-preference validator and the send-time
// sanitiser. Pure (no DOM, no React) so the preferences API route can use it too.
//
// The rule the sanitiser enforces (AI-M2, AP-H13): unless `/tasks/edit-styles` said `enabled === true` for this user
// (flag off, an iOS-only rollout, a failed fetch, an old backend's 404), a create body carries no `edit_style` and no
// animated caption template. The user's stored choice is kept, so it comes back when the flag does.
import {
  DEFAULT_EDIT_STYLE,
  editStyleToJson,
  FX_CAPTION_TEMPLATES,
  INTENSITIES,
  isFxTemplate,
  type Censor,
  type EditStyle,
  type FxCaptionTemplate,
  type GradeId,
  type Intensity,
  type Mood,
} from "@/lib/fx/core/types";

export const MOODS: readonly Mood[] = ["hype", "chill", "dramatic", "funny", "inspiring", "dark", "aggressive"];
export const GRADES: readonly GradeId[] = ["none", "punchy", "warm", "teal_orange", "noir", "vhs", "dream"];
export const CENSORS: readonly Censor[] = ["off", "bleep", "bleep_bar", "bleep_pixelate"];

/**
 * The legacy template each animated template falls back to: mirror of `ass_fallback` in
 * `backend/src/caption_templates.py` (`FX_CAPTION_TEMPLATES_DICT`, §5.3). A test pins it against the backend.
 */
export const FX_ASS_FALLBACK: Readonly<Record<FxCaptionTemplate, string>> = {
  marker_circle: "default",
  emoji_drop: "tiktok",
  slam: "mrbeast",
  behind_head: "minimal",
  stat_counter: "hormozi",
  kinetic_stack: "mrbeast",
  decode_glitch: "neon",
  highlighter_swipe: "minimal",
  face_doodles: "default",
};

/** Templates that only look right with a face in the picture (§5.3 `needs_face`). */
export const FX_NEEDS_FACE: ReadonlySet<FxCaptionTemplate> = new Set(["behind_head", "face_doodles"]);

/** `GET /tasks/edit-styles` (§5.2). */
export interface EditStylesInfo {
  /** The `dynamic_edits` flag for this user and platform. Nothing about dynamic edits shows unless it is exactly true. */
  enabled: boolean;
  intensities: readonly Intensity[];
  defaults: Omit<EditStyle, "intensity">;
  music_moods: readonly Mood[];
  grades: readonly GradeId[];
  templates: readonly FxCaptionTemplate[];
  /** GIF reactions need a provider configured on the server. */
  reactions_available: boolean;
}

const STYLE_DEFAULTS = Object.fromEntries(
  Object.entries(DEFAULT_EDIT_STYLE).filter(([key]) => key !== "intensity"),
) as Omit<EditStyle, "intensity">;

/** What the client assumes whenever it can't ask: nothing is offered. */
export const EDIT_STYLES_UNAVAILABLE: EditStylesInfo = {
  enabled: false,
  intensities: INTENSITIES,
  defaults: STYLE_DEFAULTS,
  music_moods: MOODS,
  grades: GRADES,
  templates: [],
  reactions_available: false,
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A response body as `EditStylesInfo`. Anything unexpected reads as "not enabled". */
export function parseEditStylesInfo(data: unknown): EditStylesInfo {
  if (!isRecord(data) || data.enabled !== true) return EDIT_STYLES_UNAVAILABLE;
  const known = <T extends string>(values: unknown, allowed: readonly T[]): T[] =>
    Array.isArray(values) ? allowed.filter((value) => values.includes(value)) : [...allowed];
  return {
    enabled: true,
    intensities: known(data.intensities, INTENSITIES),
    // The server's defaults are the contract's (§3.1); keep ours unless a value is well formed.
    defaults: { ...STYLE_DEFAULTS, ...parseEditStyleFields(data.defaults) },
    music_moods: known(data.music_moods, MOODS),
    grades: known(data.grades, GRADES),
    templates: known(data.templates, FX_CAPTION_TEMPLATES),
    reactions_available: data.reactions_available === true,
  };
}

/** `fetch("/api/tasks/edit-styles")`, never throwing: a failure or a 404 from an older backend means "not enabled". */
export async function fetchEditStylesInfo(fetcher: typeof fetch = (input, init) => fetch(input, init)): Promise<EditStylesInfo> {
  try {
    const response = await fetcher("/api/tasks/edit-styles", { cache: "no-store" });
    return response.ok ? parseEditStylesInfo(await response.json()) : EDIT_STYLES_UNAVAILABLE;
  } catch {
    return EDIT_STYLES_UNAVAILABLE;
  }
}

const BOOL_KEYS = [
  "sfx", "music", "zooms", "shake", "transitions", "speed", "grain", "progress_bar", "loop_ending", "wait_for_it", "reactions",
] as const satisfies readonly (keyof EditStyle)[];

/** The well-formed fields of a stored or received style. Unknown keys and bad values are dropped, never guessed. */
function parseEditStyleFields(value: unknown): Partial<EditStyle> {
  if (!isRecord(value)) return {};
  const out: Partial<EditStyle> = {};
  if ((INTENSITIES as readonly unknown[]).includes(value.intensity)) out.intensity = value.intensity as Intensity;
  for (const key of BOOL_KEYS) if (typeof value[key] === "boolean") out[key] = value[key];
  if (value.music_mood === "auto" || (MOODS as readonly unknown[]).includes(value.music_mood)) out.music_mood = value.music_mood as EditStyle["music_mood"];
  if (value.grade === "auto" || (GRADES as readonly unknown[]).includes(value.grade)) out.grade = value.grade as EditStyle["grade"];
  if ((CENSORS as readonly unknown[]).includes(value.censor)) out.censor = value.censor as Censor;
  return out;
}

/**
 * A full style from anything stored: a preference, a task row's `edit_style` (a dict or a JSON string, like the
 * backend's `read_edit_style`). Unknown keys are dropped, a bad value keeps its default, and nothing parseable is off.
 */
export function readEditStyle(value: unknown): EditStyle {
  let raw = value;
  if (typeof raw === "string") {
    try { raw = JSON.parse(raw); } catch { raw = null; }
  }
  return { ...DEFAULT_EDIT_STYLE, ...parseEditStyleFields(raw) };
}

/**
 * What the create screen remembers: every choice that differs from the defaults, intensity included even when off,
 * so the sub-toggles survive turning edits off and on again. `{}` is "never touched".
 */
export function editStylePreference(style: EditStyle): Partial<EditStyle> {
  const out: Record<string, unknown> = {};
  const defaults = DEFAULT_EDIT_STYLE as unknown as Record<string, unknown>;
  for (const [key, value] of Object.entries(style)) if (value !== defaults[key]) out[key] = value;
  return out as Partial<EditStyle>;
}

/** A stored preference back into the partial form, validated (the preferences body can be anything). */
export function parseEditStylePreference(value: unknown): Partial<EditStyle> | undefined {
  const fields = parseEditStyleFields(value);
  const compact = editStylePreference({ ...DEFAULT_EDIT_STYLE, ...fields });
  return Object.keys(compact).length ? compact : undefined;
}

export function isEditStyleActive(style: EditStyle): boolean {
  return style.intensity !== "off";
}

/** The caption template a request may carry: an animated one only while dynamic edits are enabled. */
export function sendableCaptionTemplate(template: string | null | undefined, enabled: boolean): string {
  const id = template || "default";
  return !enabled && isFxTemplate(id) ? FX_ASS_FALLBACK[id] : id;
}

/**
 * The dynamic-edits fields of a create body (§15 send-time sanitising). `edit_style` appears only when the style is active
 * **and** the flag said `enabled === true`; otherwise it is absent and an animated template becomes its legacy
 * fallback, so the request is exactly today's.
 */
export function fxCreateFields(
  { editStyle, captionTemplate }: { editStyle: EditStyle | null | undefined; captionTemplate: string | null | undefined },
  info: (Pick<EditStylesInfo, "enabled"> & Partial<Pick<EditStylesInfo, "reactions_available">>) | null | undefined,
): { caption_template: string; edit_style?: Partial<EditStyle> } {
  const enabled = info?.enabled === true;
  // A remembered "GIF reactions" choice the server can't serve (no provider) is not sent: the switch isn't shown.
  const usable = editStyle && editStyle.reactions && info?.reactions_available === false ? { ...editStyle, reactions: false } : editStyle;
  const style = usable ? editStyleToJson(usable) : {};
  return {
    caption_template: sendableCaptionTemplate(captionTemplate, enabled),
    ...(enabled && Object.keys(style).length > 0 ? { edit_style: style } : {}),
  };
}

/** Animated templates the server offers, in the contract's order (unknown ids from a newer server are left out). */
export function offeredFxTemplates(info: EditStylesInfo | null | undefined): FxCaptionTemplate[] {
  if (info?.enabled !== true) return [];
  return FX_CAPTION_TEMPLATES.filter((id) => info.templates.includes(id));
}
