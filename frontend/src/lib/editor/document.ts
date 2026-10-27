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
}
export interface Framing {
  aspect: "vertical" | "square" | "original";
  fit: "contain" | "cover";
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
  /** Absent on drafts cached before the editor had B-roll. */
  broll?: BRollClip[];
}
export interface ExportJob {
  id: string;
  status: "queued" | "rendering" | "completed" | "failed" | "cancelled";
  progress: number;
  error?: string;
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
  return duration <= 1.5
    ? { start: 0, end: Math.max(0.5, duration) }
    : { start: 0.12, end: Math.min(4, Math.max(1.5, duration - 0.25)) };
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
  return { ...doc, words: doc.words.flatMap(word => {
    if (!selected.has(word.id)) return [word];
    if (inserted) return [];
    inserted = true;
    return replacement;
  }) };
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
