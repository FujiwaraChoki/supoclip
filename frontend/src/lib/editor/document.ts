import { censoredWords } from "@/lib/fx/core/censor";
import { mappedBeats, mappedCues } from "@/lib/fx/core/mapping";
import {
  DEFAULT_EDIT_STYLE,
  isFxTemplate,
  type Beat,
  type EditStyle,
  type FaceTrack,
  type FxCaptionTemplate,
  type FxCue,
  type FxKind,
  type GradeId,
  type Mood,
  type MusicBed,
  type Sensitivity,
  type Timing,
} from "@/lib/fx/core/types";
import { buildWarp, type Warp } from "@/lib/fx/core/warp";

export interface Segment {
  id: string;
  start: number;
  end: number;
}
export interface CaptionWord {
  id: string;
  start: number;
  end: number;
  text: string;
  highlight: boolean;
}
export interface CaptionStyle {
  enabled: boolean;
  font:
    | "TikTokSans-Regular"
    | "TikTokSans-Bold"
    | "Poppins-ExtraBold"
    | "THEBOLDFONT";
  size: number;
  color: string;
  accent: string;
  y: number;
  background: boolean;
  wordsPerLine: number;
  /** Absent on drafts saved before the editor matched generated captions. */
  karaoke?: boolean;
  uppercase?: boolean;
  /** Caption template the clip was generated with; supplies its animation and outline. */
  template?: string;
}
/** Template styling the editor's own caption controls don't cover. */
export interface CaptionLook {
  animation: "none" | "karaoke" | "pop" | "fade" | "bounce";
  wordPop: boolean;
  wordBox: boolean;
  wordBoxColor: string | null;
  highlightColor: string | null;
  emphasisColor: string | null;
  strokeColor: string | null;
  strokeWidth: number;
  shadow: boolean;
  glow: boolean;
  backgroundColor: string | null;
  /** An FX caption template (drawn by fx-core, not by the legacy caption renderer). */
  fx?: boolean;
  fxStyle?: FxCaptionTemplate | null;
}
export interface Framing {
  aspect: "vertical" | "square" | "original";
  fit: "contain" | "cover" | "auto";
  zoom: number;
  x: number;
  y: number;
}
export interface Effects {
  brightness: number;
  contrast: number;
  saturation: number;
  blur: number;
  hue: number;
}
/** A Pexels video rendition; the server only downloads from Pexels' CDN. */
export interface BRollFootage {
  provider: "pexels";
  id: number;
  width: number;
  height: number;
  duration: number;
  fileUrl: string;
  pageUrl: string;
  thumbnail: string;
  credit: string;
}
/**
 * Stock footage over the clip from `start` to `end` on the source (clean.mp4)
 * timeline, so it follows cuts like captions do. `offset` is where in the
 * footage it begins.
 */
export interface BRollClip {
  id: string;
  start: number;
  end: number;
  offset: number;
  query: string;
  footage: BRollFootage;
  /** Combined pieces dissolve only at the original cutaway's outer edges. */
  fadeIn?: boolean;
  fadeOut?: boolean;
  fadeDuration?: number | null;
}
export interface EditDocument {
  version: 1;
  segments: Segment[];
  words: CaptionWord[];
  captions: CaptionStyle;
  framing: Framing;
  effects: Effects;
  volume: number;
  muted: boolean;
  /** Headline shown at the top for the first seconds; absent on very old drafts. */
  hookTitle?: string;
  /** Hook visibility in edited/output seconds. Missing preserves the generated default. */
  hookTiming?: { start: number; end: number } | null;
  /** Normalized offsets from the original text anchors; independent font scales. */
  textTransforms?: Partial<Record<TextLayer, TextTransform>>;
  /** Placement overrides for individual caption occurrences. */
  captionTransforms?: Record<string, CaptionTransform>;
  /** Absent on drafts cached before the editor had B-roll. */
  broll?: BRollClip[];
  /** Dynamic edits. Absent (never null on the wire) when the clip has none; see DocFx. */
  fx?: DocFx | null;
  /** Interview caption rows (source seconds), seeded with the session; absent on other clips. See CaptionRows. */
  captionRows?: CaptionRows | null;
}
/**
 * Per-shot caption rows of an interview clip (mirror of `editor_document.CaptionRows`): the look captions sit on the
 * row of the shot they start in while they keep their automatic position (`captions.y` still `y`, the caption layer
 * not moved vertically, the full vertical frame); a caption's own transform pins that caption. See studioCaptionRows.
 */
export interface CaptionRows {
  y: number;
  rows: { start: number; end: number; y: number }[];
}
/**
 * Dynamic edits of one draft (mirror of `editor_document.DocFx`). Beats and cues are on the source (clean.mp4, S)
 * timeline like B-roll, so they follow cuts; `mappedFx` puts them on the edit (E) timeline. Nested contract objects
 * keep their snake_case because they are shared with the clip sidecar.
 */
export interface DocFx {
  version: "fx-v1";
  style: EditStyle;
  seed: number;
  mood: Mood | null;
  sensitivity: Sensitivity;
  timing: Timing;
  beats: Beat[];
  cues: FxCue[];
  /** Auto cue ids the user deleted; the resolver never brings them back. */
  suppressed: string[];
  /** Kinds switched off by the toggles; their cues stay stored but are not drawn or heard. */
  disabled: FxKind[];
  /** Global, anchored to the final (F) timeline. */
  music: MusicBed | null;
  grade: GradeId;
  grain: number;
  progressBar: boolean;
  progressColor: string;
  loopEnding: "none" | "crossfade";
  hookTitleSuppressed: boolean;
}
export const FX_KINDS: readonly FxKind[] = ["accent", "zoom", "shake", "flash", "glitch", "desaturate", "transition",
  "sfx", "bleep", "speed", "freeze", "sticker", "reaction"];
/** The studio session's face track: normalised to the base video it was measured on, on its S clock. */
export interface SessionFaceTrack {
  base: "layout" | "clean";
  track: FaceTrack;
}
export type TextLayer = "hook" | "captions";
export interface TextTransform { x: number; y: number; scale: number }
export interface CaptionTransform extends TextTransform { size?: number | null }
export const textTransform = (doc: EditDocument, layer: TextLayer): TextTransform =>
  doc.textTransforms?.[layer] ?? { x: 0, y: 0, scale: 1 };
/** Keep the inspector's size in output units, including extreme legacy base sizes. */
export function setCaptionVisualSize(doc: EditDocument, size: number, captionId?: string): EditDocument {
  size = clamp(size, 5, 528);
  if (captionId) return { ...doc, captionTransforms: { ...doc.captionTransforms, [captionId]: { ...(doc.captionTransforms?.[captionId] ?? textTransform(doc, "captions")), size, scale: 1 } } };
  const base = clamp(doc.captions.size, Math.max(20, Math.ceil(size / 4)), Math.min(132, Math.floor(size / 0.25)));
  return { ...doc, captions: { ...doc.captions, size: base }, textTransforms: { ...doc.textTransforms, captions: { ...textTransform(doc, "captions"), scale: size / base } } };
}
export interface ExportJob {
  id: string;
  status: "queued" | "rendering" | "completed" | "failed" | "cancelled";
  progress: number;
  error?: string;
}
export interface AIEditUsage {
  plan: string;
  limit: number | null;
  used: number;
  remaining: number | null;
  resetsAt: string | null;
  allowed: boolean;
}

export interface EditorState {
  status: "unprepared" | "preparing" | "ready" | "failed";
  basis: string;
  error?: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  hasAudio: boolean;
  waveform: number[];
  draft: { revision: number; document: EditDocument };
  original: EditDocument;
  jobs: ExportJob[];
  captionLooks?: Record<string, CaptionLook>;
  /** Whether the server can make AI edits (an Anthropic key is configured). */
  aiEditing?: boolean;
  aiEditUsage?: AIEditUsage;
  hasLayout?: boolean;
  landscapeRecommended?: boolean;
  /** Which preparation produced the media; changes when the session is rebuilt. */
  mediaVersion?: string;
  /** Set when the prepared source is shorter than the clip's own footage. */
  sourceIncomplete?: { duration: number; expected: number };
  /** The draft a rebuild replaced, kept on the server for download. */
  draftBackup?: { revision: number; duration: number | null; savedAt: string };
  /** Face boxes of the session's base video (from the clip or "Analyse face"). */
  faceTrack?: SessionFaceTrack | null;
  /** Set once the person matte (matte.mp4) is in storage; changes when it is rebuilt. */
  matteVersion?: string | null;
  /** The prepared layout bakes a Ken Burns zoom (no baseline pushes are added on top). */
  layoutHasZoompan?: boolean;
}
export const DEFAULT_EFFECTS: Effects = {
  brightness: 100,
  contrast: 100,
  saturation: 100,
  blur: 0,
  hue: 0,
};
export const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));
export const editDuration = (doc: EditDocument) =>
  doc.segments.reduce((total, s) => total + s.end - s.start, 0);
/** Decode source-continuous splits together without changing the editable draft. */
export function renderSegments(segments: Segment[]): Segment[] {
  const result: Segment[] = [];
  for (const segment of segments) {
    const previous = result.at(-1);
    if (previous && Math.abs(previous.end - segment.start) < 1e-9) previous.end = segment.end;
    else result.push({ ...segment });
  }
  return result;
}
/** Hook titles are timed on the edited output, including their fade margins. */
export function hookRange(doc: EditDocument) {
  const duration = editDuration(doc);
  if (doc.hookTiming) {
    const start = clamp(doc.hookTiming.start, 0, Math.max(0, duration - .04));
    return { start, end: clamp(doc.hookTiming.end, start + Math.min(.04, duration), duration) };
  }
  return duration <= 1.5
    ? { start: 0, end: Math.max(0.5, duration) }
    : { start: 0.12, end: Math.min(4, Math.max(1.5, duration - 0.25)) };
}
/** Move one title edge, preserving a visible interval and the edited clip's bounds. */
export function trimHook(doc: EditDocument, edge: "start" | "end", seconds: number): EditDocument {
  const range = hookRange(doc), minimum = Math.min(.1, editDuration(doc));
  const value = edge === "start" ? clamp(seconds, 0, range.end - minimum) : clamp(seconds, range.start + minimum, editDuration(doc));
  return { ...doc, hookTiming: { ...range, [edge]: value } };
}
export const timecode = (time: number) =>
  `${Math.floor(Math.max(0, time) / 60)}:${(Math.max(0, time) % 60).toFixed(2).padStart(5, "0")}`;
export const uid = () => crypto.randomUUID();
export function outputSize(doc: EditDocument, width: number, height: number) {
  if (doc.framing.aspect === "vertical") return { width: 1080, height: 1920 };
  if (doc.framing.aspect === "square") return { width: 1080, height: 1080 };
  const scale = Math.min(1, 1920 / Math.max(width, height));
  return {
    width: Math.max(2, Math.floor((width * scale) / 2) * 2),
    height: Math.max(2, Math.floor((height * scale) / 2) * 2),
  };
}
export function frameRect(
  frame: Framing,
  sw: number,
  sh: number,
  width: number,
  height: number,
) {
  const scale =
    (frame.fit === "cover" ? Math.max : Math.min)(width / sw, height / sh) *
    frame.zoom;
  const w = Math.round((sw * scale) / 2) * 2,
    h = Math.round((sh * scale) / 2) * 2;
  return {
    x: (width - w) * frame.x,
    y: (height - h) * frame.y,
    width: w,
    height: h,
  };
}
export function mappedWords(doc: EditDocument): CaptionWord[] {
  const result: CaptionWord[] = [];
  let cursor = 0;
  let previousEnd: number | null = null;
  let previousWords = new Map<string, CaptionWord>();
  const orderedWords = [...doc.words].sort((a, b) => a.start - b.start);
  for (const segment of doc.segments) {
    const continuous = previousEnd !== null && Math.abs(segment.start - previousEnd) < 1e-6;
    const currentWords = new Map<string, CaptionWord>();
    for (const word of orderedWords) {
      const start = Math.max(segment.start, word.start),
        end = Math.min(segment.end, word.end);
      if (end > start && word.text.trim()) {
        const piece = {
          ...word,
          start: cursor + start - segment.start,
          end: cursor + end - segment.start,
        };
        const previous = continuous ? previousWords.get(word.id) : undefined;
        if (previous && Math.abs(previous.end - piece.start) < 1e-6) {
          previous.end = piece.end;
          currentWords.set(word.id, previous);
        } else {
          result.push(piece);
          currentWords.set(word.id, piece);
        }
      }
    }
    cursor += segment.end - segment.start;
    previousEnd = segment.end;
    previousWords = currentWords;
  }
  return result;
}
/** Fixed-size word groups, chunked exactly as the generation renderer does. */
export function captionGroups(words: CaptionWord[], count: number) {
  const groups: CaptionWord[][] = [];
  for (let i = 0; i < words.length; i += count)
    groups.push(words.slice(i, i + count));
  return groups;
}
/** A source-continuous run survives a split; repeated footage has its own ID. */
export function captionKey(doc: EditDocument, group: CaptionWord[]): string {
  let cursor = 0;
  for (const segment of renderSegments(doc.segments)) {
    cursor += segment.end - segment.start;
    if (group[0].start < cursor - 1e-9) return JSON.stringify([segment.id, group[0].id]);
  }
  return JSON.stringify([doc.segments.at(-1)?.id, group[0].id]);
}
export function captionTransform(doc: EditDocument, group: CaptionWord[]): CaptionTransform {
  return doc.captionTransforms?.[captionKey(doc, group)] ?? textTransform(doc, "captions");
}
export function captionAt(doc: EditDocument, time: number) {
  return captionGroups(mappedWords(doc), doc.captions.wordsPerLine)
    .find(words => time >= words[0].start && time < words[words.length - 1].end);
}

/** Keep local placement with the same spoken phrase when its text/IDs change. */
export function remapCaptionTransforms(before: EditDocument, after: EditDocument): EditDocument {
  if (!before.captionTransforms || !Object.keys(before.captionTransforms).length) return after;
  const previous = captionGroups(mappedWords(before), before.captions.wordsPerLine)
    .map(words => ({ words, key: captionKey(before, words) }));
  const placements: Record<string, CaptionTransform> = {};
  for (const words of captionGroups(mappedWords(after), after.captions.wordsPerLine)) {
    const key = captionKey(after, words);
    if (after.captionTransforms?.[key]) { placements[key] = after.captionTransforms[key]; continue; }
    const run = JSON.parse(key)[0];
    let best = 0;
    for (const old of previous) {
      const placement = before.captionTransforms[old.key];
      if (!placement || JSON.parse(old.key)[0] !== run) continue;
      const overlap = Math.min(words.at(-1)!.end, old.words.at(-1)!.end) - Math.max(words[0].start, old.words[0].start);
      if (overlap > best) { best = overlap; placements[key] = placement; }
    }
  }
  return { ...after, captionTransforms: placements };
}
/** The spoken word stays active until the next word in its group starts. */
export function activeWordIndex(group: CaptionWord[], time: number) {
  for (let i = group.length - 1; i >= 0; i--)
    if (time >= group[i].start) return i;
  return -1;
}
export function outputTime(
  doc: EditDocument,
  segmentIndex: number,
  sourceTime: number,
) {
  return (
    doc.segments
      .slice(0, segmentIndex)
      .reduce((sum, s) => sum + s.end - s.start, 0) +
    sourceTime -
    doc.segments[segmentIndex].start
  );
}
export function splitSegment(
  doc: EditDocument,
  index: number,
  at: number,
): EditDocument {
  const segment = doc.segments[index];
  if (!segment || at - segment.start < 0.04 || segment.end - at < 0.04)
    return doc;
  const segments = [...doc.segments];
  segments.splice(
    index,
    1,
    { ...segment, end: at },
    { id: uid(), start: at, end: segment.end },
  );
  return { ...doc, segments };
}
/** Correcting a phrase retains existing time anchors; inserted words share only the replaced span. */
export function replaceWords(
  words: CaptionWord[],
  text: string,
  duration: number,
): CaptionWord[] {
  const tokens = text.trim().split(/\s+/).filter(Boolean);
  // LCS anchors preserve unchanged words, including those after an insertion/deletion.
  const rows = words.length + 1,
    cols = tokens.length + 1;
  if (rows * cols > 1_000_000)
    throw new Error("Edit this long transcript one word at a time.");
  const table = Array.from({ length: rows }, () => new Uint16Array(cols));
  for (let i = words.length - 1; i >= 0; i--)
    for (let j = tokens.length - 1; j >= 0; j--)
      table[i][j] =
        words[i].text === tokens[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
  const anchors: [number, number][] = [[-1, -1]];
  let i = 0,
    j = 0;
  while (i < words.length && j < tokens.length) {
    if (words[i].text === tokens[j]) {
      anchors.push([i++, j++]);
    } else if (table[i + 1][j] >= table[i][j + 1]) i++;
    else j++;
  }
  anchors.push([words.length, tokens.length]);
  const result: CaptionWord[] = [];
  for (let k = 1; k < anchors.length; k++) {
    const [prevI, prevJ] = anchors[k - 1],
      [nextI, nextJ] = anchors[k];
    const count = nextJ - prevJ - 1;
    const start =
      nextI > prevI + 1 ? words[prevI + 1].start : (words[prevI]?.end ?? 0);
    const end =
      nextI > prevI + 1
        ? words[nextI - 1].end
        : (words[nextI]?.start ?? duration);
    for (let n = 0; n < count; n++) {
      const from = start + (Math.max(0.01, end - start) * n) / count;
      result.push({
        id: uid(),
        text: tokens[prevJ + n + 1].slice(0, 120),
        start: clamp(from, 0, duration - 0.01),
        end: clamp(
          start + (Math.max(0.01, end - start) * (n + 1)) / count,
          from + 0.001,
          duration,
        ),
        highlight: false,
      });
    }
    if (nextI < words.length) result.push({ ...words[nextI] });
  }
  return result;
}

/** Replace one displayed phrase using original source timings, including after cuts. */
export function replaceCaptionGroup(doc: EditDocument, ids: string[], text: string, duration: number): EditDocument {
  const selected = new Set(ids);
  const byId = new Map(doc.words.map(word => [word.id, word]));
  const words = [...selected].flatMap(id => byId.has(id) ? [byId.get(id)!] : []);
  if (!words.length) return doc;
  let replacement: CaptionWord[];
  if (words.every((word, index) => !index || word.start >= words[index - 1].start)) {
    replacement = replaceWords(words, text, duration);
  } else {
    // A phrase may cross reordered segments. Align the typed words in display
    // order, then put each result back inside its original source-word slot.
    const slots = words.map((word, index) => ({ ...word, start: index, end: index + 1 }));
    replacement = replaceWords(slots, text, slots.length).map(word => {
      const index = Math.min(words.length - 1, Math.floor((word.start + word.end) / 2));
      const source = words[index], span = source.end - source.start, minimum = Math.min(0.001, span / 2);
      const start = source.start + clamp(word.start - index, 0, 1 - minimum / span) * span;
      return { ...word, start, end: Math.max(start + minimum, source.start + clamp(word.end - index, 0, 1) * span) };
    });
  }
  replacement.sort((a, b) => a.start - b.start);
  let inserted = false;
  return remapCaptionTransforms(doc, { ...doc, words: doc.words.flatMap(word => {
    if (!selected.has(word.id)) return [word];
    if (inserted) return [];
    inserted = true;
    return replacement;
  }) });
}

export const BROLL_MIN_SECONDS = 0.2;
export const BROLL_CONTINUATION_MIN_SECONDS = 0.04;
export const BROLL_FADE_SECONDS = 0.25;
export const brollMinimum = (clip: BRollClip) =>
  clip.fadeIn === false || clip.fadeOut === false ? BROLL_CONTINUATION_MIN_SECONDS : BROLL_MIN_SECONDS;
export const brollFadeDuration = (clip: BRollClip) =>
  clip.fadeDuration ?? Math.min(BROLL_FADE_SECONDS, (clip.end - clip.start) / 3);
export const brollOf = (doc: EditDocument) => doc.broll ?? [];
export const footageKey = (footage: BRollFootage) =>
  `${footage.id}/${footage.width}x${footage.height}`;
export const footageUrl = (endpoint: string, footage: BRollFootage) =>
  `${endpoint}/broll/footage/${footage.id}/${footage.width}x${footage.height}.mp4`;

/** The B-roll clip on screen at a source time, if any. */
export function brollAt(doc: EditDocument, sourceTime: number) {
  return brollOf(doc).find((b) => sourceTime >= b.start && sourceTime < b.end) ?? null;
}

/**
 * How opaque a B-roll clip is at a source time: it dissolves in over its first
 * and out over its last quarter second, as the export does.
 */
export function brollAlpha(clip: BRollClip, sourceTime: number) {
  const fade = brollFadeDuration(clip);
  if (fade <= 0) return 1;
  return clamp(Math.min(
    clip.fadeIn === false ? 1 : (sourceTime - clip.start) / fade,
    clip.fadeOut === false ? 1 : (clip.end - sourceTime) / fade,
  ), 0, 1);
}

/** Room for a B-roll clip around `at`: from the previous clip's end to the next one's start. */
export function brollGap(doc: EditDocument, at: number, duration: number, ignore?: string) {
  let start = 0,
    end = duration;
  for (const b of brollOf(doc)) {
    if (b.id === ignore) continue;
    if (b.end <= at) start = Math.max(start, b.end);
    else if (b.start > at) end = Math.min(end, b.start);
    else return null;
  }
  return { start, end };
}

/**
 * Insert footage at `at` (source seconds) for up to `length` seconds, fitted
 * into the free space there and the footage's own length. Null when no room.
 */
export function insertBroll(
  doc: EditDocument,
  footage: BRollFootage,
  at: number,
  duration: number,
  query = "",
  length = 3,
): { doc: EditDocument; id: string } | null {
  const gap = brollGap(doc, at, duration);
  if (!gap) return null;
  const start = clamp(at, gap.start, gap.end);
  const end = Math.min(gap.end, start + Math.min(length, footage.duration));
  if (end - start < BROLL_MIN_SECONDS - 1e-9) return null;
  const id = uid();
  const clip: BRollClip = { id, start, end, offset: 0, query, footage };
  const broll = [...brollOf(doc), clip].sort((a, b) => a.start - b.start);
  return { doc: { ...doc, broll }, id };
}

/**
 * Move or resize a B-roll clip, kept inside the clip, clear of its neighbours,
 * and no longer than its footage (after its offset).
 */
export function placeBroll(
  doc: EditDocument,
  id: string,
  start: number,
  end: number,
  duration: number,
): EditDocument {
  const clip = brollOf(doc).find((b) => b.id === id);
  if (!clip) return doc;
  const others = brollOf(doc).filter((b) => b.id !== id);
  const before = Math.max(0, ...others.filter((b) => b.end <= clip.start + 1e-6).map((b) => b.end));
  const after = Math.min(duration, ...others.filter((b) => b.start >= clip.end - 1e-6).map((b) => b.start));
  const minimum = brollMinimum(clip);
  const maxLength = Math.max(minimum, clip.footage.duration - clip.offset);
  let nextStart = clamp(start, before, after - minimum);
  let nextEnd = clamp(end, nextStart + minimum, Math.min(after, nextStart + maxLength));
  if (Math.abs(end - start - (clip.end - clip.start)) < 1e-6) {
    // A move keeps the length; clamping one edge shifts the other.
    const length = Math.min(clip.end - clip.start, after - before);
    nextStart = clamp(start, before, after - length);
    nextEnd = nextStart + length;
  }
  return {
    ...doc,
    broll: brollOf(doc).map((b) => (b.id === id ? { ...b, start: nextStart, end: nextEnd } : b)),
  };
}

/** Map a track edge back to source time; a right edge belongs to the preceding segment. */
function sourceAtTrackTime(doc: EditDocument, time: number, edge: "start" | "end") {
  const items = sequence(doc);
  const at = clamp(time, 0, editDuration(doc));
  const index = items.findIndex(item => at >= item.start && (edge === "end" ? at <= item.end : at < item.end));
  const i = index < 0 ? items.length - 1 : index, item = items[i];
  // A split with no source-time jump is still continuous footage. Clamp to
  // that whole run, so dragging a continuation cannot snap to the split edge.
  let first = i, last = i;
  while (first > 0 && Math.abs(items[first - 1].segment.end - items[first].segment.start) < 1e-6) first--;
  while (last + 1 < items.length && Math.abs(items[last].segment.end - items[last + 1].segment.start) < 1e-6) last++;
  return { source: item.segment.start + at - item.start, segment: { ...item.segment, start: items[first].segment.start, end: items[last].segment.end } };
}

/** Timeline gestures use edit seconds; persisted B-roll stays anchored to source footage. */
export function placeBrollOnTimeline(
  doc: EditDocument, id: string, piece: { start: number; end: number; offset: number },
  mode: "move" | "start" | "end", delta: number, duration: number,
): EditDocument {
  const clip = brollOf(doc).find(clip => clip.id === id);
  if (!clip) return doc;
  const edge = mode === "end" ? "end" : "start";
  const origin = clip.start + piece.offset - clip.offset + (edge === "end" ? piece.end - piece.start : 0);
  const target = sourceAtTrackTime(doc, piece[edge] + delta, edge);
  // At a cut, keep the grabbed piece inside the destination's kept source
  // range instead of leaving only a sliver before a removed interval.
  const visible = Math.min(piece.end - piece.start, target.segment.end - target.segment.start);
  const destination = mode === "move" ? clamp(target.source, target.segment.start, target.segment.end - visible) : target.source;
  const shift = destination - origin;
  const placed = placeBroll(doc, id, clip.start + (mode === "end" ? 0 : shift), clip.end + (mode === "start" ? 0 : shift), duration);
  if (mode !== "start") return placed;
  // Head trimming keeps the remaining stock frames in place.
  return { ...placed, broll: brollOf(placed).map(b => b.id === id ? { ...b, offset: Math.max(0, clip.offset + b.start - clip.start) } : b) };
}

/** B-roll pieces on the output timeline, mirroring the server's mapped_broll. */
export function mappedBroll(doc: EditDocument) {
  const pieces: {
    segmentId: string;
    clip: BRollClip;
    start: number;
    end: number;
    offset: number;
    fadeIn: boolean;
    fadeOut: boolean;
    fadeDuration: number;
  }[] = [];
  let cursor = 0;
  const ordered = [...brollOf(doc)].sort((a, b) => a.start - b.start);
  for (const segment of doc.segments) {
    for (const clip of ordered) {
      const start = Math.max(segment.start, clip.start),
        end = Math.min(segment.end, clip.end);
      if (end - start >= BROLL_CONTINUATION_MIN_SECONDS - 1e-9)
        pieces.push({
          segmentId: segment.id,
          clip,
          start: cursor + start - segment.start,
          end: cursor + end - segment.start,
          offset: clip.offset + start - clip.start,
          fadeIn: clip.fadeIn !== false && start - clip.start < 0.02,
          fadeOut: clip.fadeOut !== false && clip.end - end < 0.02,
          fadeDuration: brollFadeDuration(clip),
        });
    }
    cursor += segment.end - segment.start;
  }
  return pieces;
}

/** Segments laid out back to back on the edit (output) timeline. */
export function sequence(doc: EditDocument) {
  let cursor = 0;
  return doc.segments.map((segment, index) => {
    const start = cursor;
    cursor += segment.end - segment.start;
    return { segment, index, start, end: cursor };
  });
}

/** The segment and source time playing at an edit (output) time. */
export function locate(doc: EditDocument, time: number) {
  const items = sequence(doc);
  const total = items.length ? items[items.length - 1].end : 0;
  const at = clamp(time, 0, Math.max(0, total - 0.001));
  const item = items.find((i) => at >= i.start && at < i.end) ?? items[items.length - 1];
  return { index: item.index, source: item.segment.start + at - item.start };
}

// ------------------------------------------------------------------------------------------- dynamic edits (fx)

/**
 * "The user removed dynamic edits": intensity off, no cues or beats, every kind disabled. Written by "Remove all
 * edits"; blocks re-seeding from the clip (an absent `fx` would be seeded again).
 */
export function inertDocFx(previous?: DocFx | null): DocFx {
  return {
    version: "fx-v1",
    style: { ...(previous?.style ?? DEFAULT_DOC_FX.style), intensity: "off" },
    seed: previous?.seed ?? 0,
    mood: null,
    sensitivity: "normal",
    timing: previous?.timing ?? "word",
    beats: [],
    cues: [],
    suppressed: [],
    disabled: [...FX_KINDS],
    music: null,
    grade: "none",
    grain: 0,
    progressBar: false,
    progressColor: previous?.progressColor ?? DEFAULT_DOC_FX.progressColor,
    loopEnding: "none",
    hookTitleSuppressed: false,
  };
}
export const DEFAULT_DOC_FX: DocFx = {
  version: "fx-v1",
  style: DEFAULT_EDIT_STYLE,
  seed: 0,
  mood: null,
  sensitivity: "normal",
  timing: "word",
  beats: [],
  cues: [],
  suppressed: [],
  disabled: [],
  music: null,
  grade: "none",
  grain: 0,
  progressBar: false,
  progressColor: "#FF7A1A",
  loopEnding: "none",
  hookTitleSuppressed: false,
};

/** Segments with source-continuous splits merged (== editor_document.continuous_segments). */
export const continuousSegments = renderSegments;

/** Cues of enabled kinds on the edit (E) timeline, sorted by (start, id) (== editor_document.mapped_fx). */
export function mappedFx(doc: EditDocument): FxCue[] {
  const fx = doc.fx;
  if (!fx?.cues.length) return [];
  const disabled = new Set(fx.disabled);
  return mappedCues(continuousSegments(doc.segments), fx.cues.filter((cue) => !disabled.has(cue.kind)));
}

/** Beats on the edit (E) timeline (timeline lanes, beat list). */
export function mappedFxBeats(doc: EditDocument): Beat[] {
  return doc.fx?.beats.length ? mappedBeats(continuousSegments(doc.segments), doc.fx.beats) : [];
}

/**
 * True when rendering this draft needs fx-core (== editor_document.document_needs_fx): an FX caption template, or
 * dynamic edits that draw or sound (cues of enabled kinds, music, a grade, grain, the progress bar or a loop ending).
 */
export function documentNeedsFx(doc: EditDocument): boolean {
  if (isFxTemplate(doc.captions.template)) return true;
  const fx = doc.fx;
  if (!fx) return false;
  if (fx.music !== null || fx.grade !== "none" || fx.grain > 0 || fx.progressBar || fx.loopEnding !== "none") return true;
  // Like the server, only cues that survive the cuts count: a draft whose cues were all cut away renders the legacy way.
  return planCues(doc).length > 0;
}

/** == editor_fx_render.MAX_PLAN_CUES (FxPlan.cues max_length). */
const MAX_PLAN_CUES = 400;
const planCueCache = new WeakMap<EditDocument, FxCue[]>();
/** The cues a render draws: `mappedFx`, capped like the server's plan (== editor_fx_render.studio_plan). Memoised. */
function planCues(doc: EditDocument): FxCue[] {
  let cues = planCueCache.get(doc);
  if (!cues) {
    cues = mappedFx(doc).slice(0, MAX_PLAN_CUES);
    planCueCache.set(doc, cues);
  }
  return cues;
}

const warpCache = new WeakMap<EditDocument, Warp>();
/** The speed ramps and freezes of the draft (E → F), == studio_scene's warp. Identity when it has none. */
export function fxWarp(doc: EditDocument): Warp {
  let warp = warpCache.get(doc);
  if (!warp) {
    warp = buildWarp(planCues(doc), Math.max(1 / 30, editDuration(doc)));
    warpCache.set(doc, warp);
  }
  return warp;
}

/** Length of the finished clip in final (F) seconds: the edit duration after freezes and speed ramps. */
export const finalDuration = (doc: EditDocument): number => fxWarp(doc).outDuration;

/** Bleep spans on the edit timeline, used to censor caption text (word timing only, like the server). */
export function mappedBleepSpans(doc: EditDocument): [number, number][] {
  if (!doc.fx || doc.fx.timing !== "word") return [];
  return planCues(doc).filter((cue) => cue.kind === "bleep" && cue.end > cue.start).map((cue) => [cue.start, cue.end]);
}

/** Mapped words with bleeped words censored ("f***"), as the server's ASS and fx-core captions draw them. */
export function censoredMappedWords(doc: EditDocument): CaptionWord[] {
  const words = mappedWords(doc);
  const spans = mappedBleepSpans(doc);
  return spans.length ? censoredWords(words, spans) : words;
}

/**
 * The hook title is hidden when a full-width caption style owns the top band (resolver hook_title_suppressed). Only
 * an FX render honours the flag: a draft that renders the legacy way keeps its title, as on the server.
 */
export const hookTitleSuppressed = (doc: EditDocument): boolean => !!doc.fx?.hookTitleSuppressed && documentNeedsFx(doc);

export { studioScene, type StudioSceneOptions } from "@/lib/fx/studio-scene";
