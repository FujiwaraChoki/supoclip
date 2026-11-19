import {
  activeWordIndex,
  captionGroups,
  captionAt,
  captionTransform,
  censoredMappedWords,
  documentNeedsFx,
  frameRect,
  hookRange,
  hookTitleSuppressed,
  outputSize,
  replaceWords,
  editDuration,
  textTransform,
  type TextLayer,
  type CaptionLook,
  type CaptionTransform,
  type CaptionWord,
  type EditDocument,
  type SessionFaceTrack,
} from "./document";
import { loadFxAtlas } from "@/lib/fx/assets-client";
import { createBrowserHost, createFxCanvas, loadFxFont, type BrowserHost } from "@/lib/fx/browser-host";
import { reactionRect } from "@/lib/fx/core/constants";
import { captionHitBoxes, clockAt, type FxClock } from "@/lib/fx/core/frame";
import { HOOK, hookBounds, hookLayout, hookOps, hookVariant } from "@/lib/fx/core/hook";
import type { EmojiAtlas, FxCanvas, FxContext2D, FxImage } from "@/lib/fx/core/host";
import { rasterize } from "@/lib/fx/core/raster";
import { renderFrame } from "@/lib/fx/core/render";
import { prepareScene, type PreparedScene } from "@/lib/fx/core/scene";
import { isFxTemplate, type ReactionCue } from "@/lib/fx/core/types";
import { toOutput } from "@/lib/fx/core/warp";
import { studioScene } from "@/lib/fx/studio-scene";

const groupCache = new WeakMap<
  EditDocument,
  ReturnType<typeof captionGroups>
>();
const lookCache = new WeakMap<
  EditDocument,
  { looks?: Record<string, CaptionLook>; look: ReturnType<typeof resolveLook> }
>();
const loaded = new Map<string, Promise<void>>();
export function loadEditorFont(name: string) {
  if (!loaded.has(name))
    loaded.set(
      name,
      (async () => {
        const font = new FontFace(
          `Editor-${name}`,
          `url(/api/fonts/${encodeURIComponent(name)})`,
        );
        await font.load();
        document.fonts.add(font);
      })().catch((error) => {
        loaded.delete(name);
        throw error;
      }),
    );
  return loaded.get(name)!;
}

// Used until the editor state delivers the templates (and for drafts without one).
const FALLBACK_LOOK: CaptionLook = {
  animation: "karaoke",
  wordPop: true,
  wordBox: false,
  wordBoxColor: null,
  highlightColor: null,
  emphasisColor: null,
  strokeColor: "#000000",
  strokeWidth: 3,
  shadow: true,
  glow: false,
  backgroundColor: null,
};
const HOOK_SIDE_MARGIN = 0.07, HOOK_TOP_MARGIN = 0.12;

/** libass sizes fonts by ascender + descender, not the em box CSS uses. */
function setAssFont(ctx: CanvasRenderingContext2D, font: string, size: number) {
  ctx.font = `${size}px "Editor-${font}", sans-serif`;
  const metrics = ctx.measureText("A"),
    height = metrics.fontBoundingBoxAscent + metrics.fontBoundingBoxDescent;
  const pixels = height > 0 ? size * size / height : size;
  ctx.font = `${pixels}px "Editor-${font}", sans-serif`;
  return pixels;
}

export interface TextDraft { hookText?: string; captionWords?: CaptionWord[] }

function wrapCaptionWords(ctx: CanvasRenderingContext2D, words: CaptionWord[], uppercase: boolean | undefined, width: number) {
  const lines: CaptionWord[][] = [];
  const space = ctx.measureText(" ").width;
  let line: CaptionWord[] = [], length = 0;
  for (const word of words) {
    const w = ctx.measureText(uppercase ? word.text.toUpperCase() : word.text).width;
    if (line.length && length + space + w > width) { lines.push(line); line = []; length = 0; }
    length += (line.length ? space : 0) + w;
    line.push(word);
  }
  if (line.length) lines.push(line);
  return lines;
}

/** The native field supplies caret/selection; these are the same font metrics as the canvas. */
export function textEditorLayout(ctx: CanvasRenderingContext2D, doc: EditDocument, width: number, height: number, time: number, layer: TextLayer, draft?: TextDraft, fx?: HookFx): TextEditorLayout {
  if (layer === "hook" && documentNeedsFx(doc)) {
    // The title fx-core draws: its own font and metrics, the caret in the type's colour.
    const prepared = hookScene(doc, undefined, width, height, fx);
    const layout = hookLayout(prepared, draft?.hookText);
    const variant = layout?.variant ?? hookVariant(prepared.scene.hook?.template);
    const px = layout?.px ?? variant.sizeU * prepared.u;
    return {
      fontSize: px, wrapWidth: Math.max(1, (layout?.w ?? HOOK.maxWidth * width) - 2 * variant.padX * px), lineHeight: px * variant.lineHeight,
      lines: layout?.lines.map((line) => line.text) ?? [], fontFamily: layout?.family ?? prepared.family(prepared.scene.hook?.font ?? "Poppins-ExtraBold"),
      caretColor: variant.style === "stack" ? variant.ink : variant.fill,
    };
  }
  const group = captionAt(doc, time) ?? [];
  const transform: CaptionTransform = group.length ? captionTransform(doc, group) : textTransform(doc, "captions");
  const hook = hookTitleLayout(draft?.hookText === undefined ? doc : { ...doc, hookTitle: draft.hookText }, width, height);
  const size = layer === "hook" ? hook.px : Math.max(1, Math.round((transform.size ?? doc.captions.size) * width * transform.scale / 1080));
  const fontSize = setAssFont(ctx, doc.captions.font, size);
  const wrapWidth = Math.max(1, layer === "hook" ? hook.usable : width - 120);
  const lines = layer === "hook" ? hook.lines : wrapCaptionWords(ctx, draft?.captionWords ?? group, doc.captions.uppercase, wrapWidth).map(line => line.map(word => word.text).join(" "));
  return { fontSize, wrapWidth, lineHeight: size * 1.2, lines };
}

export function prepareTextDraft(doc: EditDocument, time: number, layer: TextLayer, text: string): TextDraft {
  if (layer === "captions") return { captionWords: replaceWords(captionAt(doc, time) ?? [], text, editDuration(doc)) };
  return { hookText: text.trim().replace(/\s+/g, " ") };
}
export interface TextEditorLayout {
  fontSize: number; wrapWidth: number; lineHeight: number; lines: string[];
  /** CSS family and caret colour when they differ from the caption font and colour (the fx-core hook card). */
  fontFamily?: string; caretColor?: string;
}
/** What the fx-core hook card needs beyond the document: the session's face track and the source size. */
export interface HookFx { session?: EditorFxSession; sourceWidth: number; sourceHeight: number; looks?: Record<string, CaptionLook> }
const hookScene = (doc: EditDocument, looks: Record<string, CaptionLook> | undefined, width: number, height: number, fx?: HookFx) =>
  editorFxScene(doc, fx?.looks ?? looks, width, height, fx?.sourceWidth ?? width, fx?.sourceHeight ?? height, fx?.session);

export function formatHookEditorText(doc: EditDocument, width: number, height: number, text: string, fx?: HookFx) {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return text;
  if (documentNeedsFx(doc)) {
    // Lines as the card breaks them; text written without spaces keeps its own breaks.
    const lines = hookLayout(hookScene(doc, undefined, width, height, fx), words.join(" "))?.lines.map((line) => line.text) ?? [];
    return (lines.length && lines.join(" ") === words.join(" ") ? lines.join("\n") : words.join(" ")) + (/\s$/.test(text) ? " " : "");
  }
  const layout = hookTitleLayout({ ...doc, hookTitle: words.join(" ") }, width, height);
  let cursor = 0;
  return layout.lines.map(line => { const count = line.trim().split(/\s+/).length; const value = words.slice(cursor, cursor + count).join(" "); cursor += count; return value; }).join("\n") + (/\s$/.test(text) ? " " : "");
}

/** Linear keyframes, as ASS `\t` interpolates them. */
function keyframes(t: number, points: [number, number][]) {
  if (t <= points[0][0]) return points[0][1];
  for (let i = 1; i < points.length; i++) {
    const [t1, v1] = points[i],
      [t0, v0] = points[i - 1];
    if (t <= t1) return v0 + ((v1 - v0) * (t - t0)) / (t1 - t0);
  }
  return points[points.length - 1][1];
}

/** Mirrors `editor_caption_template` and the generation renderer's pixel maths. */
function resolveLook(doc: EditDocument, looks?: Record<string, CaptionLook>) {
  const style = doc.captions,
    look = looks?.[style.template ?? "default"] ?? FALLBACK_LOOK;
  const animation = style.karaoke
    ? "karaoke"
    : look.animation === "karaoke"
      ? "none"
      : look.animation;
  const tiedEmphasis =
    (look.emphasisColor ?? look.highlightColor) === look.highlightColor;
  // The background toggle decides whether the template's box colour applies.
  const backgroundColor = style.background
    ? (look.backgroundColor ?? "#00000080")
    : null;
  return {
    ...look,
    animation,
    backgroundColor,
    emphasisColor: tiedEmphasis
      ? style.accent
      : (look.emphasisColor ?? style.accent),
    outlineColor: look.strokeColor ?? "#000000",
    backColor: backgroundColor ?? "#00000080",
  };
}
function cachedLook(doc: EditDocument, looks?: Record<string, CaptionLook>) {
  const cached = lookCache.get(doc);
  if (cached && cached.looks === looks) return cached.look;
  const look = resolveLook(doc, looks);
  lookCache.set(doc, { looks, look });
  return look;
}

/** Stock footage drawn over the whole frame, under the captions. */
export interface BRollLayer {
  width: number;
  height: number;
  alpha: number;
  /** Draws the footage; `target` is the context to draw into (dynamic edits may compose the picture off screen). */
  draw: (x: number, y: number, width: number, height: number, target?: CanvasRenderingContext2D) => void;
}

/** A decoded frame of some video, ready for drawImage. */
export interface EditorMediaFrame {
  source: CanvasImageSource;
  width: number;
  height: number;
}

/** What the studio session knows about its media, for dynamic edits (studioScene options). */
export interface EditorFxSession {
  /** The video the picture is drawn from. */
  base: "layout" | "clean";
  faceTrack?: SessionFaceTrack | null;
  /** matte.mp4 is loaded: behind-the-head text is cut out around the person. */
  matte?: boolean;
  /** Stored ids of the reaction cues with a GIF (undefined: every resolved reaction cue). */
  reactions?: readonly string[] | null;
}

/** Per-frame inputs of a dynamic-edit frame beyond the picture itself. */
export interface EditorFxFrame {
  /** Final (F) seconds of this frame. Default: the warp's toOutput(time). */
  o?: number;
  session?: EditorFxSession;
  /** The person matte (luma = person) of the base video at this frame's source time. */
  matte?: EditorMediaFrame | null;
  /** The reaction GIF frame for a reaction cue on screen (mapped id). */
  reaction?: (cue: ReactionCue) => EditorMediaFrame | null;
}

type DrawPicture = (rect: ReturnType<typeof frameRect>, target: CanvasRenderingContext2D) => void;

const effectsFilter = (doc: EditDocument, canvasWidth: number) => {
  const fx = doc.effects;
  return `brightness(${fx.brightness}%) contrast(${fx.contrast}%) saturate(${fx.saturation}%) blur(${(fx.blur * canvasWidth) / 1080}px) hue-rotate(${fx.hue}deg)`;
};

export function drawEditorFrame(
  ctx: CanvasRenderingContext2D,
  doc: EditDocument,
  sourceWidth: number,
  sourceHeight: number,
  time: number,
  draw: DrawPicture,
  looks?: Record<string, CaptionLook>,
  broll?: BRollLayer | null,
  textLayers: "all" | "none" = "all",
  fx?: EditorFxFrame,
) {
  if (documentNeedsFx(doc)) {
    drawFxFrame(ctx, doc, sourceWidth, sourceHeight, time, draw, looks, broll ?? null, fx ?? {});
    if (textLayers === "all") drawEditorTextLayer(ctx, doc, sourceWidth, sourceHeight, time, looks, "all", false, undefined, fx?.session);
    return;
  }
  const { width, height } = ctx.canvas;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "black";
  ctx.fillRect(0, 0, width, height);
  ctx.save();
  ctx.filter = effectsFilter(doc, width);
  draw(frameRect(doc.framing, sourceWidth, sourceHeight, width, height), ctx);
  ctx.restore();
  if (broll && broll.alpha > 0 && broll.width > 0 && broll.height > 0) {
    // Fill the frame (cover), centred, as the export's scale+crop does.
    const scale = Math.max(width / broll.width, height / broll.height),
      w = broll.width * scale,
      h = broll.height * scale;
    ctx.save();
    ctx.globalAlpha = broll.alpha;
    broll.draw((width - w) / 2, (height - h) / 2, w, h, ctx);
    ctx.restore();
  }
  if (textLayers === "all") drawEditorTextLayer(ctx, doc, sourceWidth, sourceHeight, time, looks, "all", false);
}

// ------------------------------------------------------------------------------------------- dynamic edits (fx-core)

/** Fonts the fx-core drawers use (== fx_chain.DRAWER_FONT_STEMS), registered as "FX-<stem>". */
export const FX_DRAWER_FONTS = ["TikTokSans-Bold", "TikTokSans-Regular", "Poppins-ExtraBold", "THEBOLDFONT", "ArchivoBlack-Regular"] as const;

let fxEpoch = 0;
let fxAtlas: EmojiAtlas | null = null;
const fxListeners = new Set<() => void>();
const bumpFx = () => {
  fxEpoch += 1;
  for (const listener of fxListeners) listener();
};
/** Called whenever fonts or the emoji atlas finish loading (layouts are measured again). */
export function onEditorFxResources(listener: () => void): () => void {
  fxListeners.add(listener);
  return () => {
    fxListeners.delete(listener);
  };
}

/**
 * Loads what fx-core draws with: the drawer fonts, the caption font and the emoji atlas. Each part that loads makes
 * the next frame lay its text out again with the real metrics. Failures leave fallbacks in place (no emoji, Noto).
 */
export async function loadEditorFxResources(doc: EditDocument): Promise<void> {
  const stems = new Set<string>([...FX_DRAWER_FONTS, doc.captions.font]);
  const fonts = [...stems].map((stem) => loadFxFont(stem).then(bumpFx));
  const atlas = fxAtlas ? Promise.resolve() : loadFxAtlas().then((loaded) => {
    fxAtlas = loaded;
    bumpFx();
  });
  const results = await Promise.allSettled([...fonts, atlas]);
  const failed = results.find((result) => result.status === "rejected");
  if (failed) throw (failed as PromiseRejectedResult).reason;
}

interface FrameSources {
  doc: EditDocument;
  sourceWidth: number;
  sourceHeight: number;
  out: { width: number; height: number };
  draw: DrawPicture;
  broll: BRollLayer | null;
  effects: string;
  reactions: { rect: [number, number, number, number]; frame: EditorMediaFrame }[];
  matte: EditorMediaFrame | null;
}
interface HostEntry {
  host: BrowserHost;
  current: FrameSources | null;
  /** The scene being drawn (frame 0 of another scene, e.g. another clip or an older edit, is never blended in). */
  drawing: PreparedScene | null;
  frame0: { scene: PreparedScene; canvas: FxCanvas } | null;
  mask: FxCanvas | null;
  person: FxCanvas | null;
}
const hosts = new WeakMap<object, HostEntry>();

const asCanvasContext = (ctx: FxContext2D) => ctx as unknown as CanvasRenderingContext2D;
const canvasOf = (ctx: FxContext2D) => (ctx as unknown as { canvas: { width: number; height: number } }).canvas;

/** The CSS filter in force (the grade the host set), or "" when none. */
function currentFilter(ctx: CanvasRenderingContext2D): string {
  const value = typeof ctx.filter === "string" ? ctx.filter : "";
  return value && value !== "none" ? value : "";
}

/** drawImage that covers `rect` with the centre of the frame (scale up, crop), like the server's PiP. */
function drawCover(ctx: CanvasRenderingContext2D, frame: EditorMediaFrame, rect: [number, number, number, number]) {
  const [x, y, w, h] = rect;
  if (frame.width <= 0 || frame.height <= 0 || w <= 0 || h <= 0) return;
  const scale = Math.max(w / frame.width, h / frame.height);
  const sw = w / scale, sh = h / scale;
  ctx.drawImage(frame.source, (frame.width - sw) / 2, (frame.height - sh) / 2, sw, sh, x, y, w, h);
}

/**
 * The base picture in output px, as P1 builds it on the server: black frame, the framed video with the document's
 * effects (before the grade the host already set), B-roll over it, then the reaction PiPs. `ctx` carries the picture
 * op's transform and grade filter.
 */
function drawBase(ctx: CanvasRenderingContext2D, f: FrameSources) {
  const grade = currentFilter(ctx);
  ctx.fillStyle = "black";
  ctx.fillRect(0, 0, f.out.width, f.out.height);
  ctx.filter = [f.effects, grade].filter(Boolean).join(" ") || "none";
  f.draw(frameRect(f.doc.framing, f.sourceWidth, f.sourceHeight, f.out.width, f.out.height), ctx);
  ctx.filter = grade || "none";
  const broll = f.broll;
  if (broll && broll.alpha > 0 && broll.width > 0 && broll.height > 0) {
    const scale = Math.max(f.out.width / broll.width, f.out.height / broll.height),
      w = broll.width * scale,
      h = broll.height * scale;
    const alpha = ctx.globalAlpha;
    ctx.globalAlpha = alpha * broll.alpha;
    broll.draw((f.out.width - w) / 2, (f.out.height - h) / 2, w, h, ctx);
    ctx.globalAlpha = alpha;
  }
  for (const reaction of f.reactions) drawCover(ctx, reaction.frame, reaction.rect);
}

/**
 * Only the person: the framed video keyed by the matte (luma → alpha at the matte's own resolution, scaled up with
 * smoothing). Drawn through a scratch layer so the destination-in mask never touches the rest of the frame.
 */
function drawCutout(entry: HostEntry, ctx: CanvasRenderingContext2D, f: FrameSources) {
  const matte = f.matte;
  if (!matte || matte.width <= 0 || matte.height <= 0) return;
  const mw = Math.round(matte.width), mh = Math.round(matte.height);
  if (!entry.mask || entry.mask.width !== mw || entry.mask.height !== mh) {
    entry.mask = createFxCanvas(mw, mh);
    // Read back every frame: a CPU-backed context avoids a GPU readback per frame (the first getContext decides).
    (entry.mask as unknown as { getContext(kind: "2d", options: { willReadFrequently: boolean }): unknown })
      .getContext("2d", { willReadFrequently: true });
  }
  const mctx = asCanvasContext(entry.mask.getContext("2d"));
  mctx.setTransform(1, 0, 0, 1, 0, 0);
  mctx.globalCompositeOperation = "copy";
  mctx.drawImage(matte.source, 0, 0, mw, mh);
  const pixels = mctx.getImageData(0, 0, mw, mh);
  const data = pixels.data;
  for (let i = 0; i < data.length; i += 4) data[i + 3] = data[i];
  mctx.putImageData(pixels, 0, 0);

  const size = canvasOf(ctx as unknown as FxContext2D);
  if (!entry.person || entry.person.width !== size.width || entry.person.height !== size.height)
    entry.person = createFxCanvas(size.width, size.height);
  const pctx = asCanvasContext(entry.person.getContext("2d"));
  const rect = frameRect(f.doc.framing, f.sourceWidth, f.sourceHeight, f.out.width, f.out.height);
  pctx.save();
  pctx.setTransform(1, 0, 0, 1, 0, 0);
  pctx.clearRect(0, 0, size.width, size.height);
  pctx.setTransform(ctx.getTransform());
  pctx.filter = [f.effects, currentFilter(ctx)].filter(Boolean).join(" ") || "none";
  f.draw(rect, pctx);
  pctx.filter = "none";
  pctx.globalCompositeOperation = "destination-in";
  pctx.imageSmoothingEnabled = true;
  pctx.imageSmoothingQuality = "high";
  pctx.drawImage(entry.mask as unknown as CanvasImageSource, rect.x, rect.y, rect.width, rect.height);
  pctx.restore();
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.filter = "none";
  ctx.drawImage(entry.person as unknown as CanvasImageSource, 0, 0);
  ctx.restore();
}

function hostFor(canvas: object): HostEntry {
  let entry = hosts.get(canvas);
  if (!entry) {
    const created: HostEntry = { host: null as unknown as BrowserHost, current: null, drawing: null, frame0: null, mask: null, person: null };
    created.host = createBrowserHost({
      drawBase: (ctx) => { if (created.current) drawBase(asCanvasContext(ctx), created.current); },
      drawCutout: (ctx) => { if (created.current) drawCutout(created, asCanvasContext(ctx), created.current); },
      atlas: () => fxAtlas,
      frame0: (): FxImage | null =>
        created.frame0 && created.frame0.scene === created.drawing ? created.frame0.canvas : null,
    });
    entry = created;
    hosts.set(canvas, entry);
  }
  return entry;
}

let measureHost: BrowserHost | null = null;
const sceneMeasure = () => (measureHost ??= createBrowserHost({ drawBase: () => {} }));

interface SceneKey {
  looks: Record<string, CaptionLook> | undefined;
  width: number;
  height: number;
  sourceWidth: number;
  sourceHeight: number;
  base: string;
  faceTrack: SessionFaceTrack | null | undefined;
  matte: boolean;
  reactions: readonly string[] | null | undefined;
  epoch: number;
}
const sceneCache = new WeakMap<EditDocument, { key: SceneKey; prepared: PreparedScene }[]>();
const sameKey = (a: SceneKey, b: SceneKey) =>
  (Object.keys(a) as (keyof SceneKey)[]).every((name) => a[name] === b[name]);

/** The prepared fx-core scene of a draft (memoised per document, output size, session and loaded resources). */
export function editorFxScene(doc: EditDocument, looks: Record<string, CaptionLook> | undefined,
  width: number, height: number, sourceWidth: number, sourceHeight: number, session?: EditorFxSession): PreparedScene {
  const key: SceneKey = {
    looks, width, height, sourceWidth, sourceHeight, base: session?.base ?? "clean", faceTrack: session?.faceTrack,
    matte: !!session?.matte, reactions: session?.reactions, epoch: fxEpoch,
  };
  const entries = sceneCache.get(doc) ?? [];
  const hit = entries.find((entry) => sameKey(entry.key, key));
  if (hit) return hit.prepared;
  const scene = studioScene(doc, looks, {
    width, height, sourceWidth, sourceHeight, base: session?.base ?? "clean", faceTrack: session?.faceTrack,
    matte: !!session?.matte, reactions: session?.reactions,
  });
  // The hook title is its own layer in the studio (drawEditorTextLayer), never part of the picture frame.
  const prepared = prepareScene(scene, sceneMeasure(), { detachHook: true });
  // A document is drawn by the preview, the text layers and an export at most; keep the last few variants.
  sceneCache.set(doc, [...entries.slice(-3), { key, prepared }]);
  return prepared;
}

/** The fx-core clock of an edit-time frame (F from the warp unless the caller drives F itself). */
export function editorFxClock(prepared: PreparedScene, time: number, o?: number): FxClock {
  return o === undefined ? { e: time, o: toOutput(prepared.warp, time), frame: Math.round(toOutput(prepared.warp, time) * 30) }
    : clockAt(prepared, o);
}

function drawFxFrame(ctx: CanvasRenderingContext2D, doc: EditDocument, sourceWidth: number, sourceHeight: number,
  time: number, draw: DrawPicture, looks: Record<string, CaptionLook> | undefined, broll: BRollLayer | null,
  fx: EditorFxFrame) {
  const { width, height } = ctx.canvas;
  const out = outputSize(doc, sourceWidth, sourceHeight);
  const prepared = editorFxScene(doc, looks, out.width, out.height, sourceWidth, sourceHeight, fx.session);
  const clock = editorFxClock(prepared, time, fx.o);
  const entry = hostFor(ctx.canvas);
  const rect = reactionRect(out.width, out.height);
  const pip: [number, number, number, number] = [Math.round(rect.x), Math.round(rect.y), even(rect.w), even(rect.h)];
  const reactions: FrameSources["reactions"] = [];
  if (fx.reaction) {
    for (const cue of prepared.cuesByKind.get("reaction") ?? []) {
      if (cue.kind !== "reaction" || clock.e < cue.start || clock.e > cue.end) continue;
      const frame = fx.reaction(cue);
      if (frame) reactions.push({ rect: pip, frame });
    }
  }
  entry.current = {
    doc, sourceWidth, sourceHeight, out, draw, broll, effects: effectsFilter(doc, width), reactions,
    matte: fx.session?.matte ? fx.matte ?? null : null,
  };
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "black";
  ctx.fillRect(0, 0, width, height);
  ctx.restore();
  entry.drawing = prepared;
  try {
    renderFrame(ctx as unknown as FxContext2D, prepared, clock, entry.host);
  } finally {
    entry.current = null;
    entry.drawing = null;
  }
  // The loop crossfade blends the last frames into the first one: keep frame 0 as drawn (before text layers).
  if (prepared.scene.loop_ending === "crossfade" && clock.frame === 0) {
    const copy = entry.frame0?.canvas.width === width && entry.frame0.canvas.height === height
      ? entry.frame0.canvas : createFxCanvas(width, height);
    const cctx = asCanvasContext(copy.getContext("2d"));
    cctx.clearRect(0, 0, width, height);
    cctx.drawImage(ctx.canvas, 0, 0);
    entry.frame0 = { scene: prepared, canvas: copy };
  }
}

const even = (value: number) => {
  const rounded = Math.round(value);
  return Math.max(2, rounded - (rounded % 2));
};

/** Output-px box of the FX-template caption on screen at edit time `time` (drag and resize in the preview). */
export function fxCaptionBounds(doc: EditDocument, width: number, height: number, time: number,
  looks?: Record<string, CaptionLook>): TextBounds | null {
  const prepared = editorFxScene(doc, looks, width, height, width, height);
  const [box] = captionHitBoxes(prepared, editorFxClock(prepared, time));
  return box ? { x: box.x, y: box.y, width: box.w, height: box.h } : null;
}

/**
 * Where per-shot caption rows (interview clips) put the FX caption on screen at edit time `time`, relative to the
 * caption style's y (fraction of H); 0 without rows or for a caption placed by its own transform. A caption's own
 * transform starts from here, so dragging or nudging a caption on a row never makes it jump.
 */
export function fxCaptionRowShift(doc: EditDocument, width: number, height: number, time: number,
  looks?: Record<string, CaptionLook>): number {
  if (!doc.captionRows || !doc.captions.enabled || !isFxTemplate(doc.captions.template)) return 0;
  const prepared = editorFxScene(doc, looks, width, height, width, height);
  const chunk = prepared.chunkAt(editorFxClock(prepared, time).e);
  const style = prepared.scene.caption;
  return chunk?.row !== undefined && style ? chunk.row - style.y : 0;
}

/** Transparent editable layers in the viewer; composited only when exporting. */
export function drawEditorTextLayer(
  ctx: CanvasRenderingContext2D, doc: EditDocument, sourceWidth: number, sourceHeight: number,
  time: number, looks: Record<string, CaptionLook> | undefined, layer: "hook" | "captions" | "all", clear = true, draft?: TextDraft,
  session?: EditorFxSession,
) {
  const { width, height } = ctx.canvas;
  if (clear) ctx.clearRect(0, 0, width, height);
  const out = outputSize(doc, sourceWidth, sourceHeight), look = cachedLook(doc, looks);
  ctx.save();
  ctx.scale(width / out.width, height / out.height);
  // FX templates are drawn by fx-core with the picture; only legacy captions are a text layer.
  if (layer !== "hook" && doc.captions.enabled && !isFxTemplate(doc.captions.template))
    drawCaptions(ctx, doc, out, time, look, draft?.captionWords);
  const hookDocument = draft?.hookText === undefined ? doc : { ...doc, hookTitle: draft.hookText };
  const hook = layer !== "captions" && !!hookDocument.hookTitle?.trim() && !hookTitleSuppressed(doc);
  if (hook && !documentNeedsFx(doc)) drawHookTitle(ctx, hookDocument, out, time, look);
  ctx.restore();
  if (hook && documentNeedsFx(doc)) {
    // Dynamic edits: the card fx-core draws on the server (placed clear of faces and captions), from the same code.
    const prepared = editorFxScene(doc, looks, out.width, out.height, sourceWidth, sourceHeight, session);
    rasterize(ctx as unknown as FxContext2D, hookOps(prepared, time, draft?.hookText), sceneMeasure(),
      [width / out.width, 0, 0, height / out.height, 0, 0]);
  }
}

function drawCaptions(
  ctx: CanvasRenderingContext2D,
  doc: EditDocument,
  { width, height }: { width: number; height: number },
  time: number,
  look: ReturnType<typeof resolveLook>,
  draftWords?: CaptionWord[],
) {
  let groups = groupCache.get(doc);
  if (!groups) {
    // Bleeped words are censored like the server's captions ("f***"), so preview and export agree.
    groups = captionGroups(censoredMappedWords(doc), doc.captions.wordsPerLine);
    groupCache.set(doc, groups);
  }
  const original = groups.find(
    (group) => time >= group[0].start && time < group[group.length - 1].end,
  );
  const group = draftWords ?? original;
  if (!original || !group?.length) return;
  const transform = captionTransform(doc, original);
  const style = doc.captions,
    size = Math.max(1, Math.round(((transform.size ?? style.size) * width * transform.scale) / 1080)),
    karaoke = look.animation === "karaoke",
    active = karaoke ? group[activeWordIndex(group, time)] : undefined,
    label = (text: string) => (style.uppercase ? text.toUpperCase() : text);
  const outline =
    look.strokeColor && look.strokeWidth
      ? Math.max(
          look.strokeWidth,
          Math.round((size * look.strokeWidth) / 26),
        )
      : 0,
    shadow = look.shadow ? Math.max(2, Math.floor(size / 20)) : 0,
    boxPadding = Math.max(outline + 2, Math.floor(size / 5)),
    linePadding = Math.max(outline, Math.floor(size / 6)),
    centre = Math.round(height * (style.y + transform.y));
  const t = time - group[0].start,
    remaining = group[group.length - 1].end - time;
  let scale = 1,
    alpha = 1;
  if (karaoke && look.wordPop) scale = keyframes(t, [[0, 0.92], [0.14, 1]]);
  else if (look.animation === "pop")
    scale = keyframes(t, [[0, 0.88], [0.13, 1.06], [0.25, 1]]);
  else if (look.animation === "bounce")
    scale = keyframes(t, [[0, 0.7], [0.12, 1.12], [0.24, 1]]);
  else if (look.animation === "fade")
    alpha = Math.max(0, Math.min(1, t / 0.12, remaining / 0.12));

  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(width * (0.5 + transform.x), centre);
  ctx.scale(scale, scale);
  setAssFont(ctx, style.font, size);
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  // Fit each phrase in the safe width, using the same font for preview and browser export.
  const space = ctx.measureText(" ").width;
  const lines = wrapCaptionWords(ctx, group, style.uppercase, width - 120);
  lines.forEach((words, index) => {
    const total =
      words.reduce((sum, w) => sum + ctx.measureText(label(w.text)).width, 0) +
      space * (words.length - 1);
    const y = (index - (lines.length - 1) / 2) * size * 1.2;
    if (look.backgroundColor) {
      ctx.fillStyle = look.backgroundColor;
      ctx.fillRect(
        -total / 2 - linePadding,
        y - size / 2 - linePadding,
        total + linePadding * 2,
        size + linePadding * 2,
      );
    }
    let x = -total / 2;
    for (const word of words) {
      const text = label(word.text),
        textWidth = ctx.measureText(text).width,
        isActive = word === active;
      if (isActive && look.wordBox) {
        ctx.fillStyle = look.wordBoxColor ?? style.accent;
        ctx.beginPath();
        ctx.roundRect(
          x - boxPadding,
          y - size * 0.45 - boxPadding,
          textWidth + boxPadding * 2,
          size * 0.9 + boxPadding * 2,
          boxPadding,
        );
        ctx.fill();
      } else if (!look.backgroundColor && (outline || shadow)) {
        ctx.save();
        if (shadow) {
          ctx.shadowColor = look.backColor;
          ctx.shadowOffsetX = ctx.shadowOffsetY = shadow;
        }
        if (look.glow) ctx.filter = "blur(4px)";
        ctx.strokeStyle = look.outlineColor;
        ctx.lineWidth = outline * 2;
        if (outline) ctx.strokeText(text, x, y);
        else {
          ctx.fillStyle = look.backColor;
          ctx.fillText(text, x, y);
        }
        ctx.restore();
      }
      ctx.fillStyle = isActive
        ? style.accent
        : word.highlight
          ? look.emphasisColor
          : style.color;
      ctx.fillText(text, x, y);
      x += textWidth + space;
    }
  });
  ctx.restore();
}

/** Split the title into one line, or two lines balanced around the middle. */
function balanceTitle(words: string[], maxChars: number) {
  const text = words.join(" ");
  if (text.length <= maxChars || words.length < 2) return [text];
  let best = [text],
    longest = text.length;
  for (let i = 1; i < words.length; i++) {
    const lines = [words.slice(0, i).join(" "), words.slice(i).join(" ")];
    const candidate = Math.max(lines[0].length, lines[1].length);
    if (candidate < longest) {
      longest = candidate;
      best = lines;
    }
  }
  return best;
}

export function hookTitleLayout(doc: EditDocument, width: number, height: number) {
  const title = doc.hookTitle?.trim() ?? "";
  const text = doc.captions.uppercase ? title.toUpperCase() : title;
  const captionPx = Math.max(1, Math.round((doc.captions.size * width) / 1080));
  const basePx = Math.max(34, Math.min(66, Math.floor(captionPx * 0.82)));
  const usable = width - 2 * Math.max(48, Math.floor(width * HOOK_SIDE_MARGIN));
  const maxChars = Math.max(10, Math.floor(usable / (basePx * 0.52)));
  const lines = balanceTitle(text.split(/\s+/), maxChars);
  const longest = Math.max(...lines.map(line => line.length));
  const originalPx = longest > maxChars ? Math.max(30, Math.min(basePx, Math.floor(usable / (longest * 0.52)))) : basePx;
  const transform = textTransform(doc, "hook");
  const px = originalPx * transform.scale;
  const top = Math.max(48, Math.floor(height * HOOK_TOP_MARGIN)) + transform.y * height + (originalPx - px) * lines.length * 0.6;
  return { lines, px, usable, top, x: width * (0.5 + transform.x) };
}

/** The generated clip's headline: top safe area, fading in and out over ~4s. */
function drawHookTitle(
  ctx: CanvasRenderingContext2D,
  doc: EditDocument,
  { width, height }: { width: number; height: number },
  time: number,
  look: ReturnType<typeof resolveLook>,
) {
  const { start, end } = hookRange(doc);
  if (time < start || time >= end) return;
  const style = doc.captions;
  const { lines, px, top, x: centreX } = hookTitleLayout(doc, width, height);
  const hasOutline = !!look.strokeColor && look.strokeWidth > 0,
    // Mirrors build_hook_title_ass: an opaque box only without an outline.
    boxed = !hasOutline && !!look.backgroundColor,
    outline = boxed
      ? Math.max(4, Math.floor(px / 6))
      : hasOutline
        ? Math.max(look.strokeWidth, Math.round((px * look.strokeWidth) / 26))
        : Math.max(2, Math.floor(px / 16)),
    shadow = look.shadow ? Math.max(2, Math.floor(px / 20)) : 0;
  const t = time - start;
  ctx.save();
  const fadeIn = Math.min(.16, (end - start) / 3), fadeOut = Math.min(.24, (end - start) / 3);
  ctx.globalAlpha = Math.max(0, Math.min(1, t / fadeIn, (end - time) / fadeOut));
  const lineHeight = px * 1.2,
    scale = look.wordPop ? keyframes(t, [[0, 0.9], [fadeIn, 1]]) : 1;
  ctx.translate(centreX, top + (lineHeight * lines.length) / 2);
  ctx.scale(scale, scale);
  setAssFont(ctx, style.font, px);
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  const space = ctx.measureText(" ").width;
  lines.forEach((line, index) => {
    const y = (index - (lines.length - 1) / 2) * lineHeight,
      words = line.split(" "),
      total =
        words.reduce((sum, w) => sum + ctx.measureText(w).width, 0) +
        space * (words.length - 1);
    if (boxed) {
      ctx.fillStyle = look.outlineColor;
      ctx.fillRect(
        -total / 2 - outline,
        y - lineHeight / 2 - outline / 2,
        total + outline * 2,
        lineHeight + outline,
      );
    }
    let x = -total / 2;
    for (const word of words) {
      if (!boxed) {
        ctx.save();
        if (shadow) {
          ctx.shadowColor = look.backColor;
          ctx.shadowOffsetX = ctx.shadowOffsetY = shadow;
        }
        ctx.strokeStyle = look.outlineColor;
        ctx.lineWidth = outline * 2;
        ctx.strokeText(word, x, y);
        ctx.restore();
      }
      // Numbers take the accent; the export also accents power words.
      ctx.fillStyle = /\d/.test(word) ? look.emphasisColor : style.color;
      ctx.fillText(word, x, y);
      x += ctx.measureText(word).width + space;
    }
  });
  ctx.restore();
}

export interface TextBounds { x: number; y: number; width: number; height: number }

/** Selection geometry in output pixels, measured with the renderer's font. */
export function textLayerBounds(ctx: CanvasRenderingContext2D, doc: EditDocument, width: number, height: number, time: number, layer: TextLayer, looks?: Record<string, CaptionLook>, draft?: TextDraft, fx?: HookFx): TextBounds | null {
  const look = cachedLook(doc, looks);
  if (layer === "hook") {
    if (!doc.hookTitle?.trim() || hookTitleSuppressed(doc)) return null;
    if (documentNeedsFx(doc)) {
      const box = hookBounds(hookScene(doc, looks, width, height, fx), draft?.hookText) ?? hookBounds(hookScene(doc, looks, width, height, fx));
      return box ? { x: box.x, y: box.y, width: box.w, height: box.h } : null;
    }
    const { lines, px, top, x } = hookTitleLayout(draft?.hookText === undefined ? doc : { ...doc, hookTitle: draft.hookText }, width, height);
    setAssFont(ctx, doc.captions.font, px);
    const pad = Math.max(5, Math.round(px * (look.strokeWidth || 2) / 26)) + 3;
    const w = Math.max(...lines.map(line => ctx.measureText(line).width)) + pad * 2;
    return { x: x - w / 2, y: top - pad, width: w, height: lines.length * px * 1.2 + pad * 2 };
  }
  if (isFxTemplate(doc.captions.template))
    return doc.captions.enabled ? fxCaptionBounds(doc, width, height, time, looks) : null;
  const original = captionAt(doc, time);
  const group = draft?.captionWords ?? original;
  if (!original || !group?.length || !doc.captions.enabled) return null;
  const transform = captionTransform(doc, original);
  const px = Math.max(1, Math.round((transform.size ?? doc.captions.size) * width * transform.scale / 1080));
  setAssFont(ctx, doc.captions.font, px);
  const space = ctx.measureText(" ").width;
  const lines = wrapCaptionWords(ctx, group, doc.captions.uppercase, width - 120);
  const longest = Math.max(...lines.map(line => line.reduce((sum, word) => sum + ctx.measureText(doc.captions.uppercase ? word.text.toUpperCase() : word.text).width, 0) + space * (line.length - 1)));
  const pad = Math.max(px / 5, look.strokeWidth * px / 26, 5) + 3;
  const w = longest + pad * 2, h = lines.length * px * 1.2 + pad * 2;
  return { x: width * (0.5 + transform.x) - w / 2, y: height * (doc.captions.y + transform.y) - h / 2, width: w, height: h };
}
