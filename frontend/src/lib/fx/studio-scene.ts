// The studio's FxScene: everything fx-core needs to draw a draft in the browser (preview, browser export), built from
// the EditDocument exactly like the server builds it for a studio export (editor_fx_render.studio_scene →
// media.fx_chain.build_scene). Both sides replay the same fixtures (shared/fx/fixtures/scene/). Pure: no DOM, no fetch.
//
// Clocks: the document stores S (clean.mp4 seconds). The scene is on E (edit seconds after segment cuts), with the
// warp to F. Cues and words go S → E through the continuous segments; the session face track is resampled onto E
// through locate(e) (AR-B4), then placed into the output frame with the picture rect.
import {
  clamp,
  continuousSegments,
  hookRange,
  locate,
  mappedBroll,
  mappedFx,
  outputSize,
  type CaptionLook,
  type EditDocument,
  type Framing,
  type SessionFaceTrack,
} from "@/lib/editor/document";
import { censoredWords } from "./core/censor";
import { mappedCaptionRows } from "./core/mapping";
import { FACE_MAX_GAP } from "./core/scene";
import {
  captionLookFor,
  isFxTemplate,
  isListedFxTemplate,
  type CaptionPlacement,
  type CaptionRow,
  type FaceTrack,
  type FxCaptionStyle,
  type FxCue,
  type FxHookTitle,
  type FxScene,
  type FxWord,
  type MatteWindow,
  type ReactionCue,
} from "./core/types";
import { buildWarp, isNontrivial, warpToJson } from "./core/warp";

export interface StudioSceneOptions {
  /** Output size. Default: outputSize(doc, sourceWidth, sourceHeight). */
  width?: number;
  height?: number;
  /** Size of the base video (layout.mp4 or clean.mp4). */
  sourceWidth: number;
  sourceHeight: number;
  /** Which video is the base picture: layout.mp4 (auto vertical framing with a prepared layout) or clean.mp4. */
  base?: "layout" | "clean";
  /** The session's face track; used only when it was measured on `base`. */
  faceTrack?: SessionFaceTrack | null;
  /** matte.mp4 is available for `base`: behind-the-head text is cut out around the person. */
  matte?: boolean;
  /** The B-roll footage is drawn (default true); without it there are no B-roll windows. */
  broll?: boolean;
  /**
   * Stored (S) ids of the reaction cues whose GIF is available. Undefined: every reaction cue with a resolved GIF.
   * The server leaves out reaction cues it has no file for, so no frame is drawn around a missing PiP.
   */
  reactions?: readonly string[] | null;
}

/** E samples per second of the resampled session face track (== editor_fx_render.SESSION_FACE_FPS). */
export const STUDIO_FACE_FPS = 6;
/** == editor_fx_render.LOCATE_EPSILON: the last instant of a timeline belongs to its last segment. */
const LOCATE_EPSILON = 0.001;
/** == editor_fx_render.MAX_PLAN_CUES (FxPlan.cues max_length). */
const MAX_PLAN_CUES = 400;

/** Python's round(): half to even. Sizes must match the server's exactly. */
export function pyRound(value: number): number {
  const floor = Math.floor(value);
  if (value - floor === 0.5) return floor % 2 === 0 ? floor : floor + 1;
  return Math.round(value);
}
/** Python's round(value, places) for display-sized values. */
const roundTo = (value: number, places: number) => {
  const k = 10 ** places;
  return pyRound(value * k) / k;
};

const HEX = /^#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$/;
function hex6(value: unknown, fallback: string): string {
  const match = HEX.exec(String(value ?? "").trim());
  return match ? `#${match[1].toUpperCase()}` : fallback;
}
function rgba(value: unknown): string | null {
  const match = HEX.exec(String(value ?? "").trim());
  return match ? `#${match[1].toUpperCase()}${(match[2] ?? "FF").toUpperCase()}` : null;
}

const DEFAULT_ACCENT = "#FFE000", DEFAULT_COLOR = "#FFFFFF", DEFAULT_FONT = "TikTokSans-Bold", DEFAULT_BOX = "#00000080";
const MAX_STROKE_PX = 40, MAX_SIZE_PX = 400, MAX_PLACEMENT_SCALE = 4, MIN_PLACEMENT_SCALE = 1e-3;

/** == edit_fx.caption_style.LOOKS (studio: the font is the document's, seeded from these). */
const STUDIO_LOOKS = {
  subtle: { font: "TikTokSans-Bold", strokeEm: 0.07, uppercase: false, words: 4 },
  bold: { font: "Poppins-ExtraBold", strokeEm: 0, uppercase: false, words: 3 },
  max: { font: "THEBOLDFONT", strokeEm: 0.09, uppercase: true, words: 3 },
} as const;
/** == edit_fx.caption_style.LOOK_PILLS: Bold is the dark pill for every listed style. */
const LOOK_PILL = "#170904D9";
/** == edit_fx.caption_style.look_accent. */
function lookAccent(template: string, look: "subtle" | "bold" | "max"): string {
  if (template === "highlighter_swipe") return look === "bold" ? "#34FD87" : "#FFE45C";
  return { subtle: "#F2C14E", bold: "#FFE00B", max: "#FCFB2B" }[look];
}

/** The template fields the FX caption look reads (CaptionLook carries them: caption_looks). */
type TemplateLook = Pick<CaptionLook, "strokeColor" | "strokeWidth" | "shadow" | "backgroundColor" | "highlightColor">;
/** Used only when the session did not send the template's look (never expected for FX ids). */
const FALLBACK_LOOK: TemplateLook = {
  strokeColor: "#000000", strokeWidth: 3, shadow: false, backgroundColor: null, highlightColor: null,
};

/**
 * == editor_fx_render.studio_caption_style (edit_fx.caption_style.fx_caption_style_from_document): the editor's
 * caption style over the template look. The captions layer's transform moves the centre (y) and scales the size;
 * per-caption transforms become placements (a size override folds into the scale). fx-core keeps captions
 * horizontally centred on their placement. Null for legacy templates (ASS / text layer) and disabled captions.
 */
export function studioCaptionStyle(doc: EditDocument, looks: Record<string, CaptionLook> | undefined | null,
  width: number): FxCaptionStyle | null {
  const captions = doc.captions;
  const templateId = captions.template || "default";
  if (!captions.enabled || !isFxTemplate(templateId)) return null;
  const look: TemplateLook = looks?.[templateId] ?? FALLBACK_LOOK;
  const layer = doc.textTransforms?.captions;
  const y = layer ? clamp(captions.y + layer.y, 0, 1) : captions.y;
  const x = layer ? clamp(layer.x, -1, 1) : 0;
  const size = layer ? captions.size * layer.scale : captions.size;
  const sizePx = clamp(Math.max(1, pyRound((size * width) / 1080)), 1, MAX_SIZE_PX);
  const baseStroke = Math.trunc(Number(look.strokeWidth ?? 3) || 0);
  const hasStroke = look.strokeColor !== null && look.strokeColor !== undefined;
  const wordsPerLine = Math.trunc(Number(captions.wordsPerLine));
  const captionLook = captionLookFor(templateId, doc.fx?.style.intensity ?? "off", doc.fx?.sensitivity ?? "normal");
  if (captionLook !== "template") {
    // == fx_caption_style_from_document with a look (edit_fx.caption_style.LOOKS / LOOK_ACCENTS / LOOK_PILLS).
    const look = captionLook;
    const spec = STUDIO_LOOKS[look];
    const strokePx = roundTo(spec.strokeEm * sizePx, 2);
    const pill = look === "bold" ? LOOK_PILL : null;
    return {
      template: templateId.slice(0, 50),
      font: String(captions.font || spec.font).slice(0, 80),
      size_px: sizePx,
      color: hex6(captions.color, DEFAULT_COLOR),
      accent: hex6(captions.accent || lookAccent(templateId, look), DEFAULT_ACCENT),
      stroke_color: strokePx > 0 ? "#000000" : null,
      stroke_px: strokePx,
      shadow: true,
      background: pill && captions.background ? pill : null,
      y: clamp(y, 0, 1),
      x,
      words_per_line: clamp(Number.isFinite(wordsPerLine) ? wordsPerLine : spec.words, 1, 8),
      uppercase: spec.uppercase || !!captions.uppercase,
      enabled: true,
      placements: captionPlacements(doc, { x, y: clamp(y, 0, 1), scale: layer ? layer.scale : 1 }),
      look,
      rows: studioCaptionRows(doc),
    };
  }
  return {
    template: templateId.slice(0, 50),
    font: String(captions.font || DEFAULT_FONT).slice(0, 80),
    size_px: sizePx,
    color: hex6(captions.color, DEFAULT_COLOR),
    accent: hex6(captions.accent || look.highlightColor, DEFAULT_ACCENT),
    stroke_color: hasStroke ? hex6(look.strokeColor, "#000000") : null,
    stroke_px: !hasStroke || baseStroke <= 0 ? 0 : Math.min(MAX_STROKE_PX, Math.max(baseStroke, pyRound((sizePx * baseStroke) / 26))),
    shadow: !!look.shadow,
    background: captions.background ? rgba(look.backgroundColor || DEFAULT_BOX) : null,
    y: clamp(y, 0, 1),
    x,
    words_per_line: clamp(Number.isFinite(wordsPerLine) ? wordsPerLine : 4, 1, 8),
    uppercase: !!captions.uppercase,
    enabled: true,
    placements: captionPlacements(doc, { x, y: clamp(y, 0, 1), scale: layer ? layer.scale : 1 }),
    look: "template",
    rows: [],
  };
}

/** captions.y within this of the seeded position still counts as automatic (== CAPTION_ROWS_Y_TOLERANCE). */
const CAPTION_ROWS_Y_TOLERANCE = 5e-4;

/**
 * The draft's interview caption rows on E (== editor_document.studio_caption_rows), or [] when they do not apply: no
 * rows, captions off or not an intensity look, the user moved the captions (captions.y changed, or the caption layer
 * moved vertically) or the frame is not the full vertical picture the rows were measured on.
 */
export function studioCaptionRows(doc: EditDocument): CaptionRow[] {
  const stored = doc.captionRows;
  const captions = doc.captions;
  if (!stored || !stored.rows.length || !captions.enabled) return [];
  if (captionLookFor(captions.template || "default", doc.fx?.style.intensity ?? "off", doc.fx?.sensitivity ?? "normal") === "template")
    return [];
  const layer = doc.textTransforms?.captions;
  if (Math.abs(captions.y - stored.y) > CAPTION_ROWS_Y_TOLERANCE || (layer && layer.y !== 0)) return [];
  if (doc.framing.aspect !== "vertical" || doc.framing.zoom !== 1) return [];
  return mappedCaptionRows(continuousSegments(doc.segments), stored.rows);
}

/**
 * One CaptionPlacement per caption with its own transform, sorted by key (caption keys are
 * JSON.stringify([run, firstWordId])). A caption's own transform replaces the global one (like the legacy
 * captions), and fx-core adds a placement to the style, so x/y/scale are stored relative to the global style
 * (`style`: its x, its clamped y, the global scale). A size override folds into the scale.
 */
export function captionPlacements(doc: EditDocument,
  style: { x: number; y: number; scale: number } = { x: 0, y: doc.captions.y, scale: 1 }): CaptionPlacement[] {
  const own = doc.captionTransforms ?? {};
  return Object.keys(own).sort().map((key) => {
    const placement = own[key];
    const scale = placement.scale * (placement.size ? placement.size / doc.captions.size : 1) / style.scale;
    return {
      key: key.slice(0, 200), x: placement.x - style.x, y: placement.y + (doc.captions.y - style.y),
      scale: clamp(scale, MIN_PLACEMENT_SCALE, MAX_PLACEMENT_SCALE),
    };
  });
}

/**
 * Caption words on E (== editor_fx_render.studio_words, then build_scene's word pass): cut by the continuous
 * segments, `run` = the continuous segment they play in (the run of caption keys), censored under bleeps when the
 * timing is per word, blank words dropped.
 */
export function studioWords(doc: EditDocument, cues: readonly FxCue[]): FxWord[] {
  const ordered = [...doc.words].sort((a, b) => a.start - b.start);
  let words: FxWord[] = [];
  let cursor = 0;
  for (const segment of continuousSegments(doc.segments)) {
    for (const word of ordered) {
      const start = Math.max(segment.start, word.start), end = Math.min(segment.end, word.end);
      if (end > start && word.text.trim())
        words.push({ id: word.id, start: cursor + start - segment.start, end: cursor + end - segment.start,
          text: word.text, highlight: !!word.highlight, run: segment.id });
    }
    cursor += segment.end - segment.start;
  }
  const spans = doc.fx?.timing === "word"
    ? cues.filter((c) => c.kind === "bleep" && c.end > c.start).map((c) => [c.start, c.end] as [number, number])
    : [];
  if (spans.length) words = censoredWords(words, spans);
  const out: FxWord[] = [];
  for (const word of words) {
    if (!Number.isFinite(word.start) || !Number.isFinite(word.end)) continue;
    const text = String(word.text ?? "").slice(0, 120);
    if (!text.trim()) continue;
    out.push({
      id: String(word.id).slice(0, 40), start: word.start, end: Math.max(word.start, word.end), text,
      highlight: !!word.highlight, run: String(word.run || "clip").slice(0, 40),
    });
  }
  return out;
}

type Rows = number[][];

/**
 * Face rows of a track at time `t` of its own clock (== editor_fx_render._track_at): linear between bracketing
 * samples ≤ FACE_MAX_GAP apart holding the same number of faces with the same row lengths; else the nearer sample
 * (ties to the earlier), but nothing across a longer gap more than half a sample step from either side; the end
 * samples are held up to FACE_MAX_GAP. Values rounded to 6 decimals.
 */
export function faceRowSampler(track: FaceTrack): (t: number) => Rows {
  const ordered = [...track.samples].sort((a, b) => a[0] - b[0]);
  const times = ordered.map((s) => Number(s[0]));
  const rows = ordered.map((s) => (s[1] ?? []).filter((row) => row.length >= 4).map((row) => [...row]));
  const fps = track.fps > 0 ? track.fps : STUDIO_FACE_FPS;
  const round = (r: number[]) => r.map((v) => roundTo(v, 6));
  return (s) => {
    if (!times.length) return [];
    let i = -1;
    for (let lo = 0, hi = times.length - 1; lo <= hi;) {
      const mid = (lo + hi) >> 1;
      if (times[mid] <= s) { i = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (i < 0) return times[0] - s <= FACE_MAX_GAP ? rows[0].map(round) : [];
    if (i === times.length - 1) return s - times[i] <= FACE_MAX_GAP ? rows[i].map(round) : [];
    const ta = times[i], tb = times[i + 1], ra = rows[i], rb = rows[i + 1];
    const gap = tb - ta;
    const sameShape = ra.length === rb.length && ra.length > 0 && ra.every((a, j) => a.length === rb[j].length);
    if (sameShape && gap <= FACE_MAX_GAP) {
      const k = gap > 0 ? (s - ta) / gap : 0;
      return ra.map((a, j) => round(a.map((v, n) => v + (rb[j][n] - v) * k)));
    }
    const nearA = s - ta <= tb - s;
    const near = nearA ? ra : rb, dist = nearA ? s - ta : tb - s;
    if (gap > FACE_MAX_GAP && dist > 0.5 / fps) return [];
    return near.map(round);
  };
}

/** E times of the resampled track: k / fps over the edit, plus e − 0.001 and e at every cut (6 decimals). */
export function faceSampleTimes(doc: EditDocument, fps = STUDIO_FACE_FPS): number[] {
  const segments = continuousSegments(doc.segments);
  const total = segments.reduce((sum, s) => sum + s.end - s.start, 0);
  if (total <= 0) return [];
  const times = new Set<number>();
  for (let k = 0; k <= Math.floor(total * fps + 1e-9); k++) times.add(roundTo(k / fps, 6));
  let cursor = 0;
  segments.forEach((segment, index) => {
    if (index) {
      times.add(roundTo(Math.max(0, cursor - LOCATE_EPSILON), 6));
      times.add(roundTo(cursor, 6));
    }
    cursor += segment.end - segment.start;
  });
  return [...times].filter((t) => t >= 0 && t <= total + 1e-9).sort((a, b) => a - b);
}

/** The session track (S, picture space) on the E clock (== editor_fx_render.resample_face_track). */
export function resampleFaceTrack(doc: EditDocument, track: FaceTrack, fps = STUDIO_FACE_FPS): FaceTrack {
  const sample = faceRowSampler(track);
  return {
    version: 1, space: "picture", fps,
    samples: faceSampleTimes(doc, fps).map((e): [number, number[][]] => [e, sample(locate(doc, e).source)]),
  };
}

/** == fx_chain._face_track_to_output: picture rows placed with the normalised picture rect (x, y, w, h). */
export function placeFaceTrack(track: FaceTrack, rect: readonly [number, number, number, number]): FaceTrack {
  if (track.space === "output") return track;
  const [rx, ry, rw, rh] = rect;
  if (rx === 0 && ry === 0 && rw === 1 && rh === 1) return { ...track, space: "output" };
  const samples = track.samples.map(([t, rows]): [number, number[][]] => [t, rows.filter((row) => row.length >= 4).map((row) => {
    const values = [...row];
    values[0] = rx + values[0] * rw;
    values[1] = ry + values[1] * rh;
    values[2] = values[2] * rw;
    values[3] = values[3] * rh;
    for (let i = 5; i < Math.min(values.length, 15); i++) values[i] = (i - 5) % 2 === 0 ? rx + values[i] * rw : ry + values[i] * rh;
    return values.map((v) => roundTo(v, 4));
  })]);
  return { version: 1, space: "output", fps: track.fps, samples };
}

/** Where the base picture lands in the output, normalised (== editor_fx_render.output_geometry's rect). */
export function pictureRect(framing: Framing, sourceWidth: number, sourceHeight: number, width: number,
  height: number): [number, number, number, number] {
  const scale = (framing.fit === "cover" ? Math.max : Math.min)(width / sourceWidth, height / sourceHeight) * framing.zoom;
  const rw = Math.max(2, pyRound((sourceWidth * scale) / 2) * 2), rh = Math.max(2, pyRound((sourceHeight * scale) / 2) * 2);
  return [((width - rw) * framing.x) / width, ((height - rh) * framing.y) / height, rw / width, rh / height];
}

/** Stored (S) id of a mapped cue: a segment repeat adds "@k". */
export const storedCueId = (id: string): string => id.replace(/@\d+$/, "");

const needsFace = (cues: readonly FxCue[]) => cues.some((c) => (c.kind === "zoom" && c.anchor === "face")
  || (c.kind === "accent" && c.variant === "doodle") || (c.kind === "bleep" && c.cover !== "none"));

function readableTrack(track: unknown): track is FaceTrack {
  if (!track || typeof track !== "object") return false;
  const t = track as Partial<FaceTrack>;
  return Array.isArray(t.samples) && (t.fps === undefined || (typeof t.fps === "number" && t.fps > 0 && t.fps <= 30));
}

/** The card's type for the listed animated styles (== edit_fx.hook_title.HOOK_FONT). */
export const HOOK_FONT = "Poppins-ExtraBold";
/** A title is never set in a regular weight (== edit_fx.hook_title.HOOK_HEAVY). */
const HOOK_HEAVY: Readonly<Record<string, string>> = { "TikTokSans-Regular": "TikTokSans-Bold" };
const heavy = (font: string) => HOOK_HEAVY[font] ?? font;
const LOOK_FONTS: readonly string[] = Object.values(STUDIO_LOOKS).map((spec) => spec.font);

/**
 * The hook title fx-core draws for a draft (== editor_fx_render.studio_hook): the title at its own position plus
 * the draft's hook transform, in the draft's font and accent, shown for hookRange; null without a title or when the
 * plan suppresses it.
 */
export function studioHook(doc: EditDocument): FxHookTitle | null {
  const title = (doc.hookTitle ?? "").trim().split(/\s+/).filter(Boolean).join(" ").slice(0, 200);
  if (!title || doc.fx?.hookTitleSuppressed) return null;
  const range = hookRange(doc);
  if (!(range.end > range.start)) return null;
  const captions = doc.captions, templateId = captions.template || "default";
  const listed = isListedFxTemplate(templateId);
  const intensity = doc.fx?.style.intensity ?? "off", sensitivity = doc.fx?.sensitivity ?? "normal";
  const captionLook = captionLookFor(templateId, intensity, sensitivity);
  const look = captionLook !== "template" ? captionLook
    : sensitivity === "serious" ? "subtle"
      : intensity === "subtle" || intensity === "bold" || intensity === "max" ? intensity : "bold";
  const transform = doc.textTransforms?.hook;
  return {
    text: captions.uppercase && !listed ? title.toUpperCase() : title,
    font: listed && (!captions.font || LOOK_FONTS.includes(captions.font)) ? HOOK_FONT : heavy(String(captions.font || HOOK_FONT).slice(0, 80)),
    accent: hex6(captions.accent, "#FFE45C"),
    look,
    template: String(templateId).slice(0, 40),
    start: range.start,
    end: range.end,
    x: clamp(transform?.x ?? 0, -1, 1),
    y: clamp(transform?.y ?? 0, -1, 1),
    scale: clamp(transform?.scale ?? 1, 0.2, 4),
  };
}

/**
 * The draft as an FxScene (E time), == editor_fx_render.studio_scene: the enabled cues mapped through the cuts
 * (speed/freeze only when they warp, reactions only with a GIF), words censored under bleeps, the FX caption look,
 * the warp, B-roll and matte windows, the hook window, and the session face track resampled and placed (an empty
 * track when face cues need one and the session has none for this base: they fall back at draw time).
 */
export function studioScene(doc: EditDocument, looks: Record<string, CaptionLook> | undefined | null,
  opts: StudioSceneOptions): FxScene {
  const fx = doc.fx ?? null;
  const out = outputSize(doc, opts.sourceWidth, opts.sourceHeight);
  const W = Math.round(opts.width ?? out.width), H = Math.round(opts.height ?? out.height);
  const segments = continuousSegments(doc.segments);
  const editDuration = segments.reduce((sum, s) => sum + s.end - s.start, 0);
  const planCues = mappedFx(doc).slice(0, MAX_PLAN_CUES);
  const warp = buildWarp(planCues, Math.max(1 / 30, editDuration));
  const warped = isNontrivial(warp);
  const available = opts.reactions ? new Set(opts.reactions) : null;
  const hasReaction = (cue: ReactionCue) => available
    ? available.has(cue.id) || available.has(storedCueId(cue.id))
    : !!cue.url && !!cue.asset_id;
  const cues = planCues.filter((cue) => {
    if ((cue.kind === "speed" || cue.kind === "freeze") && !warped) return false;
    if (cue.kind === "reaction" && !hasReaction(cue)) return false;
    return true;
  });

  const matteWindows: MatteWindow[] = planCues
    .filter((c) => c.kind === "accent" && c.variant === "behind" && c.end > c.start)
    .map((c) => ({ start: c.start, end: c.end, person: !!opts.matte }))
    .sort((a, b) => a.start - b.start);

  const base = opts.base ?? "clean";
  let track: FaceTrack | null = null;
  const session = opts.faceTrack;
  if (session && session.base === base && readableTrack(session.track))
    track = resampleFaceTrack(doc, { version: 1, space: "picture", fps: session.track.fps ?? STUDIO_FACE_FPS, samples: session.track.samples });
  if (!track && needsFace(planCues)) track = { version: 1, space: "picture", fps: STUDIO_FACE_FPS, samples: [] };

  const hook = doc.hookTitle?.trim() ? hookRange(doc) : null;
  const drawnHook = studioHook(doc);
  // The hook title is placed clear of the faces on screen: an empty track when the session has none.
  if (!track && drawnHook) track = { version: 1, space: "picture", fps: STUDIO_FACE_FPS, samples: [] };
  const faceTrack = track ? placeFaceTrack(track, pictureRect(doc.framing, opts.sourceWidth, opts.sourceHeight, W, H)) : null;
  return {
    version: 1,
    width: W,
    height: H,
    edit_duration: warp.editDuration,
    out_duration: warp.outDuration,
    warp: warpToJson(warp),
    template: doc.captions.template || "default",
    cues,
    words: studioWords(doc, planCues),
    caption: studioCaptionStyle(doc, looks, W),
    grade: fx?.grade ?? "none",
    grain: fx?.grain ?? 0,
    progress_bar: fx?.progressBar ?? false,
    progress_color: fx?.progressColor ?? "#FF7A1A",
    loop_ending: fx?.loopEnding ?? "none",
    hook_window: hook ? [hook.start, hook.end] : null,
    hook_title_suppressed: fx?.hookTitleSuppressed ?? false,
    hook: drawnHook,
    face_track: faceTrack,
    broll_windows: opts.broll === false ? [] : mappedBroll(doc).map((piece) => [piece.start, piece.end] as [number, number]),
    matte_windows: matteWindows,
    seed: fx?.seed ?? 0,
  };
}
