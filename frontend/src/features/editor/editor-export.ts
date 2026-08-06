import type {
  EditorAsset,
  EditorProject,
  TextAlign,
  TimelineItem,
} from "./types";

export interface ExportProjectOptions {
  /** Receives monotonically increasing progress values from 0 through 1. */
  onProgress?: (progress: number) => void;
  signal?: AbortSignal;
}

type Mediabunny = typeof import("mediabunny");
type MediaInput = import("mediabunny").Input;
type WrappedCanvas = import("mediabunny").WrappedCanvas;

interface PreparedVideoItem {
  frames: AsyncIterator<WrappedCanvas | null>;
}

interface RenderResources {
  images: Map<string, ImageBitmap>;
  videos: Map<string, PreparedVideoItem>;
  audioAssetIds: Set<string>;
}

const AUDIO_SAMPLE_RATE = 48_000;
const AUDIO_CHANNELS = 2;
const MIN_SPEED = 0.25;
const RESOURCE_PROGRESS = 0.12;
const AUDIO_PROGRESS = 0.2;
const FRAME_PROGRESS = 0.94;
const MAX_BROWSER_EXPORT_SECONDS = 1_800;
const MAX_BROWSER_EXPORT_PIXELS = 3840 * 2160;
const MAX_SOURCE_BYTES = 256 * 1024 * 1024;
const MAX_ESTIMATED_MEMORY_BYTES = 640 * 1024 * 1024;

const BLEND_MODES: Record<TimelineItem["blendMode"], GlobalCompositeOperation> = {
  normal: "source-over",
  multiply: "multiply",
  screen: "screen",
  overlay: "overlay",
  darken: "darken",
  lighten: "lighten",
};

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

function finiteOr(value: number, fallback: number): number {
  return Number.isFinite(value) ? value : fallback;
}

function createAbortError(): Error {
  if (typeof DOMException !== "undefined") {
    return new DOMException("The export was aborted.", "AbortError");
  }

  const error = new Error("The export was aborted.");
  error.name = "AbortError";
  return error;
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw createAbortError();
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function reportProgress(
  callback: ExportProjectOptions["onProgress"],
  value: number,
): void {
  callback?.(clamp(value, 0, 1));
}

function isTimelineItemInExport(item: TimelineItem, duration: number): boolean {
  return !item.hidden && item.duration > 0 && item.start < duration && item.start + item.duration > 0;
}

function isItemActive(item: TimelineItem, time: number): boolean {
  return !item.hidden && time >= item.start && time < item.start + item.duration;
}

function sourceTimeForItem(item: TimelineItem, timelineTime: number): number {
  const elapsed = Math.max(0, timelineTime - item.start);
  return Math.max(0, item.trimStart) + elapsed * Math.max(MIN_SPEED, item.speed);
}

function absoluteSameOriginUrl(url: string): string {
  if (typeof window === "undefined") {
    throw new Error("Video export is only available in a browser.");
  }

  const absolute = new URL(url, window.location.href);
  if (absolute.origin !== window.location.origin) {
    throw new Error(`Cannot export a cross-origin asset: ${absolute.hostname}`);
  }

  return absolute.toString();
}

async function fetchAssetBlobs(
  project: EditorProject,
  assets: readonly EditorAsset[],
  options: ExportProjectOptions,
): Promise<Map<string, Blob>> {
  const assetMap = new Map(assets.map((asset) => [asset.id, asset]));
  const neededAssetIds = Array.from(
    new Set(
      project.items
        .filter((item) => isTimelineItemInExport(item, project.duration))
        .map((item) => item.assetId)
        .filter((assetId): assetId is string => Boolean(assetId)),
    ),
  );
  const blobs = new Map<string, Blob>();
  let totalBytes = 0;

  for (let index = 0; index < neededAssetIds.length; index += 1) {
    throwIfAborted(options.signal);
    const assetId = neededAssetIds[index];
    const asset = assetMap.get(assetId);
    if (!asset) throw new Error(`The project references a missing asset (${assetId}).`);

    const response = await fetch(absoluteSameOriginUrl(asset.url), {
      cache: "no-store",
      credentials: "same-origin",
      signal: options.signal,
    });
    if (!response.ok) {
      throw new Error(`Unable to load “${asset.name}” for export (${response.status}).`);
    }

    const declaredSize = Number(response.headers.get("content-length") || 0);
    if (declaredSize > 0 && totalBytes + declaredSize > MAX_SOURCE_BYTES) {
      await response.body?.cancel();
      throw new Error("The source media is too large for a safe browser export. Use shorter or smaller source files.");
    }
    const blob = await response.blob();
    totalBytes += blob.size;
    if (totalBytes > MAX_SOURCE_BYTES) {
      throw new Error("The source media is too large for a safe browser export. Use shorter or smaller source files.");
    }
    blobs.set(assetId, blob);
    reportProgress(
      options.onProgress,
      neededAssetIds.length === 0
        ? RESOURCE_PROGRESS
        : RESOURCE_PROGRESS * ((index + 1) / neededAssetIds.length),
    );
  }

  if (neededAssetIds.length === 0) reportProgress(options.onProgress, RESOURCE_PROGRESS);
  return blobs;
}

function* videoTimestamps(
  item: TimelineItem,
  duration: number,
  fps: number,
): Generator<number> {
  const frameCount = Math.ceil(duration * fps);
  for (let frame = 0; frame < frameCount; frame += 1) {
    const timelineTime = frame / fps;
    if (isItemActive(item, timelineTime)) {
      yield sourceTimeForItem(item, timelineTime);
    }
  }
}

async function prepareRenderResources(
  media: Mediabunny,
  project: EditorProject,
  assets: readonly EditorAsset[],
  blobs: ReadonlyMap<string, Blob>,
  inputs: MediaInput[],
  resources: RenderResources,
  options: ExportProjectOptions,
): Promise<void> {
  const assetMap = new Map(assets.map((asset) => [asset.id, asset]));
  const { images, videos, audioAssetIds } = resources;

  const mediaItems = project.items.filter(
    (item) =>
      isTimelineItemInExport(item, project.duration) &&
      (item.type === "video" || (item.type === "image" && item.opacity > 0)),
  );

  for (const item of mediaItems) {
    throwIfAborted(options.signal);
    if (!item.assetId) continue;
    const asset = assetMap.get(item.assetId);
    const blob = blobs.get(item.assetId);
    if (!asset || !blob) continue;

    if (item.type === "image") {
      if (!images.has(asset.id)) {
        if (typeof createImageBitmap !== "function") {
          throw new Error("This browser cannot decode images for video export.");
        }
        images.set(asset.id, await createImageBitmap(blob));
      }
      continue;
    }

    const input = new media.Input({
      source: new media.BlobSource(blob),
      formats: media.ALL_FORMATS,
    });
    inputs.push(input);

    const track = await input.getPrimaryVideoTrack();
    if (!track || !(await track.canDecode())) {
      throw new Error(`This browser cannot decode the video asset “${asset.name}”.`);
    }
    if (await input.getPrimaryAudioTrack()) audioAssetIds.add(asset.id);

    const sink = new media.CanvasSink(track, {
      alpha: true,
      poolSize: 1,
    });
    const frames = sink
      .canvasesAtTimestamps(videoTimestamps(item, project.duration, project.canvas.fps))
      [Symbol.asyncIterator]();
    videos.set(item.id, { frames });
  }
}

function audioEnvelope(item: TimelineItem, timelineTime: number): number {
  const volume = clamp(item.volume / 100, 0, 1);
  return volume * fadeEnvelope(item, timelineTime);
}

function fadeEnvelope(item: TimelineItem, timelineTime: number): number {
  const elapsed = timelineTime - item.start;
  const remaining = item.start + item.duration - timelineTime;
  const fadeIn = item.fadeIn > 0 ? clamp(elapsed / item.fadeIn, 0, 1) : 1;
  const fadeOut = item.fadeOut > 0 ? clamp(remaining / item.fadeOut, 0, 1) : 1;
  return Math.min(fadeIn, fadeOut);
}

function scheduleItemGain(
  gain: GainNode,
  item: TimelineItem,
  start: number,
  end: number,
): void {
  const boundaries = [start, end];
  const fadeInEnd = item.start + item.fadeIn;
  const fadeOutStart = item.start + item.duration - item.fadeOut;
  if (fadeInEnd > start && fadeInEnd < end) boundaries.push(fadeInEnd);
  if (fadeOutStart > start && fadeOutStart < end) boundaries.push(fadeOutStart);
  boundaries.sort((left, right) => left - right);

  gain.gain.setValueAtTime(audioEnvelope(item, boundaries[0]), boundaries[0]);
  for (const boundary of boundaries.slice(1)) {
    gain.gain.linearRampToValueAtTime(audioEnvelope(item, boundary), boundary);
  }
}

async function mixProjectAudio(
  project: EditorProject,
  assets: readonly EditorAsset[],
  blobs: ReadonlyMap<string, Blob>,
  resources: RenderResources,
  options: ExportProjectOptions,
): Promise<AudioBuffer | null> {
  const audibleItems = project.items.filter(
    (item) =>
      isTimelineItemInExport(item, project.duration) &&
      (item.type === "audio" ||
        (item.type === "video" && Boolean(item.assetId && resources.audioAssetIds.has(item.assetId)))) &&
      !item.muted &&
      item.volume > 0 &&
      Boolean(item.assetId),
  );
  if (audibleItems.length === 0) return null;
  if (typeof OfflineAudioContext === "undefined") {
    throw new Error("This browser cannot mix project audio for export.");
  }

  const frameLength = Math.max(1, Math.ceil(project.duration * AUDIO_SAMPLE_RATE));
  const context = new OfflineAudioContext(AUDIO_CHANNELS, frameLength, AUDIO_SAMPLE_RATE);
  const assetMap = new Map(assets.map((asset) => [asset.id, asset]));
  const decoded = new Map<string, AudioBuffer | null>();

  for (const item of audibleItems) {
    throwIfAborted(options.signal);
    const assetId = item.assetId;
    if (!assetId || decoded.has(assetId)) continue;
    const asset = assetMap.get(assetId);
    const blob = blobs.get(assetId);
    if (!asset || !blob) {
      decoded.set(assetId, null);
      continue;
    }

    try {
      const bytes = await blob.arrayBuffer();
      throwIfAborted(options.signal);
      decoded.set(assetId, await context.decodeAudioData(bytes));
    } catch (error) {
      if (isAbortError(error) || options.signal?.aborted) throw createAbortError();
      throw new Error(
        `This browser could not decode audio from “${asset.name}”. Export stopped to avoid a silent file.`,
        { cause: error },
      );
    }
  }

  let scheduledSources = 0;
  for (const item of audibleItems) {
    throwIfAborted(options.signal);
    const buffer = item.assetId ? decoded.get(item.assetId) : null;
    if (!buffer) continue;

    const speed = Math.max(MIN_SPEED, finiteOr(item.speed, 1));
    const timelineStart = Math.max(0, item.start);
    const skippedTimeline = Math.max(0, timelineStart - item.start);
    const sourceOffset = Math.max(0, item.trimStart + skippedTimeline * speed);
    const availableTimelineDuration = Math.max(0, (buffer.duration - sourceOffset) / speed);
    const itemTimelineDuration = Math.max(0, item.duration - skippedTimeline);
    const timelineDuration = Math.min(
      itemTimelineDuration,
      availableTimelineDuration,
      Math.max(0, project.duration - timelineStart),
    );
    if (timelineDuration <= 0) continue;

    const source = context.createBufferSource();
    const gain = context.createGain();
    source.buffer = buffer;
    source.playbackRate.value = speed;
    source.connect(gain);
    gain.connect(context.destination);

    const timelineEnd = timelineStart + timelineDuration;
    scheduleItemGain(gain, item, timelineStart, timelineEnd);
    source.start(timelineStart, sourceOffset, timelineDuration * speed);
    scheduledSources += 1;
  }

  if (scheduledSources === 0) return null;
  const rendered = await context.startRendering();
  throwIfAborted(options.signal);
  return rendered;
}

function setRoundedRectPath(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
): void {
  const safeRadius = clamp(radius, 0, Math.min(width, height) / 2);
  context.beginPath();
  context.moveTo(x + safeRadius, y);
  context.lineTo(x + width - safeRadius, y);
  context.quadraticCurveTo(x + width, y, x + width, y + safeRadius);
  context.lineTo(x + width, y + height - safeRadius);
  context.quadraticCurveTo(x + width, y + height, x + width - safeRadius, y + height);
  context.lineTo(x + safeRadius, y + height);
  context.quadraticCurveTo(x, y + height, x, y + height - safeRadius);
  context.lineTo(x, y + safeRadius);
  context.quadraticCurveTo(x, y, x + safeRadius, y);
  context.closePath();
}

function beginItemFrame(
  context: CanvasRenderingContext2D,
  project: EditorProject,
  item: TimelineItem,
  timelineTime: number,
): { width: number; height: number } {
  const width = Math.max(1, (item.transform.width / 100) * project.canvas.width);
  const height = Math.max(1, (item.transform.height / 100) * project.canvas.height);
  const x = (item.transform.x / 100) * project.canvas.width;
  const y = (item.transform.y / 100) * project.canvas.height;

  context.save();
  context.translate(x, y);
  context.rotate((item.transform.rotation * Math.PI) / 180);
  context.globalAlpha = clamp(item.opacity / 100, 0, 1) * fadeEnvelope(item, timelineTime);
  context.globalCompositeOperation = BLEND_MODES[item.blendMode] ?? "source-over";

  if (item.type === "shape" && item.shape?.kind === "circle") {
    context.beginPath();
    context.ellipse(0, 0, width / 2, height / 2, 0, 0, Math.PI * 2);
  } else {
    context.beginPath();
    context.rect(-width / 2, -height / 2, width, height);
  }
  context.clip();
  return { width, height };
}

function mediaFilter(item: TimelineItem): string {
  return [
    `brightness(${item.effects.brightness}%)`,
    `contrast(${item.effects.contrast}%)`,
    `saturate(${item.effects.saturation}%)`,
    `blur(${item.effects.blur}px)`,
    `hue-rotate(${item.effects.hue}deg)`,
  ].join(" ");
}

function drawMedia(
  context: CanvasRenderingContext2D,
  source: CanvasImageSource,
  item: TimelineItem,
  width: number,
  height: number,
): void {
  const horizontal = Math.max(5, 100 - item.crop.left - item.crop.right);
  const vertical = Math.max(5, 100 - item.crop.top - item.crop.bottom);
  const drawWidth = width * (100 / horizontal);
  const drawHeight = height * (100 / vertical);
  const drawX = -width / 2 - width * (item.crop.left / horizontal);
  const drawY = -height / 2 - height * (item.crop.top / vertical);

  context.filter = mediaFilter(item);
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.drawImage(source, drawX, drawY, drawWidth, drawHeight);
  context.filter = "none";
}

function captionTextAtTime(item: TimelineItem, timelineTime: number): string {
  const words = item.text?.content.trim().split(/\s+/).filter(Boolean) ?? [];
  if (words.length <= 5) return words.join(" ");
  const progress = clamp((timelineTime - item.start) / Math.max(item.duration, 0.01), 0, 0.9999);
  const wordIndex = Math.floor(progress * words.length);
  const start = Math.max(0, Math.min(words.length - 5, wordIndex - 1));
  return words.slice(start, start + 5).join(" ");
}

function textWidth(
  context: CanvasRenderingContext2D,
  value: string,
  letterSpacing: number,
): number {
  return context.measureText(value).width + Math.max(0, graphemes(value).length - 1) * letterSpacing;
}

function graphemes(value: string): string[] {
  if (typeof Intl.Segmenter === "function") {
    const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
    return Array.from(segmenter.segment(value), (part) => part.segment);
  }
  return Array.from(value);
}

function wrapText(
  context: CanvasRenderingContext2D,
  value: string,
  maximumWidth: number,
  letterSpacing: number,
): string[] {
  const lines: string[] = [];
  for (const paragraph of value.split("\n")) {
    const tokens = paragraph.match(/\S+\s*/gu) ?? [];
    if (tokens.length === 0) {
      lines.push("");
      continue;
    }

    let line = tokens[0]!.trimStart();
    for (const token of tokens.slice(1)) {
      const candidate = `${line}${token}`;
      if (textWidth(context, candidate, letterSpacing) <= maximumWidth) line = candidate;
      else {
        lines.push(line.trimEnd());
        line = token.trimStart();
      }
    }
    lines.push(line.trimEnd());
  }
  return lines;
}

function drawTextLine(
  context: CanvasRenderingContext2D,
  value: string,
  x: number,
  y: number,
  align: TextAlign,
  letterSpacing: number,
  strokeWidth: number,
): void {
  const width = textWidth(context, value, letterSpacing);
  let cursor = align === "left" ? x : align === "right" ? x - width : x - width / 2;

  if (letterSpacing === 0) {
    context.textAlign = align;
    if (strokeWidth > 0) context.strokeText(value, x, y);
    context.fillText(value, x, y);
    return;
  }

  context.textAlign = "left";
  for (const character of graphemes(value)) {
    if (strokeWidth > 0) context.strokeText(character, cursor, y);
    context.fillText(character, cursor, y);
    cursor += context.measureText(character).width + letterSpacing;
  }
}

function drawTextItem(
  context: CanvasRenderingContext2D,
  item: TimelineItem,
  timelineTime: number,
  width: number,
  height: number,
): void {
  const text = item.text;
  if (!text) return;
  const value = item.type === "caption" ? captionTextAtTime(item, timelineTime) : text.content;

  context.filter = "none";
  context.fillStyle = "transparent";
  context.fillStyle = text.backgroundColor || "transparent";
  context.fillRect(-width / 2, -height / 2, width, height);
  context.font = `${text.fontWeight} ${text.fontSize}px ${text.fontFamily || "Arial"}, Arial, sans-serif`;
  context.textBaseline = "middle";
  context.fillStyle = "#ffffff";
  context.fillStyle = text.color;
  context.strokeStyle = "#000000";
  context.strokeStyle = text.strokeColor;
  context.lineWidth = Math.max(0, text.strokeWidth * 2);
  context.lineJoin = "round";
  context.shadowColor = "rgba(0, 0, 0, 0.32)";
  context.shadowBlur = 12;
  context.shadowOffsetY = 2;

  const horizontalPadding = width * 0.03;
  const maximumWidth = Math.max(1, width - horizontalPadding * 2);
  const lines = wrapText(context, value, maximumWidth, text.letterSpacing);
  const lineHeight = text.fontSize * text.lineHeight;
  const blockHeight = lines.length * lineHeight;
  const startY = -blockHeight / 2 + lineHeight / 2;
  const x =
    text.align === "left"
      ? -width / 2 + horizontalPadding
      : text.align === "right"
        ? width / 2 - horizontalPadding
        : 0;

  lines.forEach((line, index) => {
    drawTextLine(
      context,
      line,
      x,
      startY + index * lineHeight,
      text.align,
      text.letterSpacing,
      text.strokeWidth,
    );
  });
  context.shadowColor = "transparent";
  context.shadowBlur = 0;
  context.shadowOffsetY = 0;
}

function drawShapeItem(
  context: CanvasRenderingContext2D,
  item: TimelineItem,
  width: number,
  height: number,
): void {
  const shape = item.shape;
  if (!shape) return;
  context.filter = "none";
  context.fillStyle = "transparent";
  context.fillStyle = shape.fill;

  if (shape.kind === "circle") {
    context.beginPath();
    context.ellipse(0, 0, width / 2, height / 2, 0, 0, Math.PI * 2);
    context.fill();
    return;
  }

  setRoundedRectPath(
    context,
    -width / 2,
    -height / 2,
    width,
    height,
    shape.borderRadius,
  );
  context.fill();
}

async function drawProjectFrame(
  context: CanvasRenderingContext2D,
  project: EditorProject,
  resources: RenderResources,
  timelineTime: number,
): Promise<void> {
  context.save();
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.globalAlpha = 1;
  context.globalCompositeOperation = "source-over";
  context.filter = "none";
  context.clearRect(0, 0, project.canvas.width, project.canvas.height);
  context.fillStyle = "#000000";
  context.fillStyle = project.canvas.background || "#000000";
  context.fillRect(0, 0, project.canvas.width, project.canvas.height);
  context.restore();

  for (const item of project.items) {
    if (!isItemActive(item, timelineTime) || item.type === "audio" || item.opacity <= 0) continue;
    const frame = beginItemFrame(context, project, item, timelineTime);

    if (item.type === "video") {
      const prepared = resources.videos.get(item.id);
      const nextFrame = prepared ? await prepared.frames.next() : null;
      if (nextFrame && !nextFrame.done && nextFrame.value) {
        drawMedia(context, nextFrame.value.canvas, item, frame.width, frame.height);
      }
    } else if (item.type === "image" && item.assetId) {
      const image = resources.images.get(item.assetId);
      if (image) drawMedia(context, image, item, frame.width, frame.height);
    } else if (item.type === "text" || item.type === "caption") {
      drawTextItem(context, item, timelineTime, frame.width, frame.height);
    } else if (item.type === "shape") {
      drawShapeItem(context, item, frame.width, frame.height);
    }

    context.restore();
  }
}

async function prepareFonts(project: EditorProject): Promise<void> {
  if (typeof document === "undefined" || !document.fonts) return;
  const requests = project.items
    .filter((item) => !item.hidden && item.text)
    .map((item) => {
      const text = item.text!;
      return document.fonts.load(`${text.fontWeight} ${text.fontSize}px ${text.fontFamily}`);
    });
  await Promise.allSettled(requests);
  await document.fonts.ready;
}

async function closeRenderResources(resources: RenderResources): Promise<void> {
  for (const bitmap of resources.images.values()) bitmap.close();
  await Promise.allSettled(
    Array.from(resources.videos.values()).map(({ frames }) => frames.return?.()),
  );
}

function estimateBrowserExportMemory(
  project: EditorProject,
  assets: readonly EditorAsset[],
  blobs: ReadonlyMap<string, Blob>,
  hasAudio: boolean,
): number {
  const pixels = project.canvas.width * project.canvas.height;
  const pixelRatio = pixels / (1920 * 1080);
  const frameRateRatio = project.canvas.fps / 30;
  const estimatedVideoBitrate = clamp(
    8_000_000 * pixelRatio * frameRateRatio,
    4_000_000,
    32_000_000,
  );
  const outputBytes = (estimatedVideoBitrate / 8) * project.duration;
  const audioPcmBytes = hasAudio
    ? project.duration * AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * Float32Array.BYTES_PER_ELEMENT
    : 0;
  const sourceBytes = Array.from(blobs.values()).reduce((total, blob) => total + blob.size, 0);
  const assetMap = new Map(assets.map((asset) => [asset.id, asset]));
  const imageAssetIds = new Set(
    project.items
      .filter((item) => item.type === "image" && item.assetId && isTimelineItemInExport(item, project.duration))
      .map((item) => item.assetId as string),
  );
  const decodedImageBytes = Array.from(imageAssetIds).reduce((total, assetId) => {
    const asset = assetMap.get(assetId);
    return total + Math.max(1, asset?.width ?? project.canvas.width)
      * Math.max(1, asset?.height ?? project.canvas.height)
      * 4;
  }, 0);
  const decodedVideoBytes = project.items
    .filter(
      (item) =>
        item.type === "video"
        && item.assetId
        && isTimelineItemInExport(item, project.duration),
    )
    .reduce((total, item) => {
      const asset = item.assetId ? assetMap.get(item.assetId) : undefined;
      const sourcePixels = Math.max(1, asset?.width ?? project.canvas.width)
        * Math.max(1, asset?.height ?? project.canvas.height);
      // Budget a conservative four decoded surfaces for every retained
      // Input/CanvasSink. Cuts intentionally count separately.
      return total + sourcePixels * 4 * 4;
    }, 0);
  const workingFrameBytes = pixels * 4 * 4;

  return outputBytes
    + audioPcmBytes
    + sourceBytes * 2
    + decodedImageBytes
    + decodedVideoBytes
    + workingFrameBytes;
}

/**
 * Renders a complete editor project to an MP4 in the browser.
 *
 * The project is composited at its native canvas size and frame rate. Asset
 * reads stay same-origin so authenticated task media remains protected by the
 * application's existing session boundary.
 */
export async function exportProject(
  project: EditorProject,
  assets: readonly EditorAsset[],
  options: ExportProjectOptions = {},
): Promise<Blob> {
  if (typeof document === "undefined") {
    throw new Error("Video export is only available in a browser.");
  }
  if (!(project.duration > 0)) throw new Error("Add media to the timeline before exporting.");
  if (project.duration > MAX_BROWSER_EXPORT_SECONDS) {
    throw new Error("Browser exports are limited to 30 minutes. Shorten the project before exporting.");
  }

  throwIfAborted(options.signal);
  reportProgress(options.onProgress, 0);

  const width = Math.max(2, Math.round(project.canvas.width / 2) * 2);
  const height = Math.max(2, Math.round(project.canvas.height / 2) * 2);
  const fps = clamp(Math.round(finiteOr(project.canvas.fps, 30)), 1, 120);
  if (width * height > MAX_BROWSER_EXPORT_PIXELS) {
    throw new Error("Browser exports are limited to 4K resolution.");
  }
  const renderProject: EditorProject = {
    ...project,
    canvas: { ...project.canvas, width, height, fps },
  };
  const inputs: MediaInput[] = [];
  const resources: RenderResources = {
    images: new Map(),
    videos: new Map(),
    audioAssetIds: new Set(),
  };
  let output: import("mediabunny").Output | null = null;
  let videoSource: import("mediabunny").CanvasSource | null = null;
  let audioSource: import("mediabunny").AudioBufferSource | null = null;

  const disposeInputs = () => {
    for (const input of inputs) {
      if (!input.disposed) input.dispose();
    }
  };
  const abortListener = () => disposeInputs();
  options.signal?.addEventListener("abort", abortListener, { once: true });

  try {
    const media = await import("mediabunny");
    throwIfAborted(options.signal);
    const videoSupported = await media.canEncodeVideo("avc", {
      width,
      height,
      bitrate: media.QUALITY_HIGH,
    });
    if (!videoSupported) throw new Error("This browser cannot encode H.264 video.");

    const blobs = await fetchAssetBlobs(renderProject, assets, options);
    const timelineMayContainAudio = renderProject.items.some(
      (item) =>
        isTimelineItemInExport(item, renderProject.duration) &&
        !item.muted &&
        item.volume > 0 &&
        (item.type === "video" || item.type === "audio"),
    );
    const preliminaryMemory = estimateBrowserExportMemory(
      renderProject,
      assets,
      blobs,
      timelineMayContainAudio,
    );
    if (preliminaryMemory > MAX_ESTIMATED_MEMORY_BYTES) {
      throw new Error(
        "This export is too large for a safe browser render. Shorten it, use a smaller canvas, or use smaller source files.",
      );
    }
    await prepareRenderResources(
      media,
      renderProject,
      assets,
      blobs,
      inputs,
      resources,
      options,
    );
    await prepareFonts(renderProject);

    const hasPotentialAudio = renderProject.items.some(
      (item) =>
        isTimelineItemInExport(item, renderProject.duration) &&
        !item.muted &&
        item.volume > 0 &&
        (item.type === "audio" ||
          (item.type === "video" && Boolean(item.assetId && resources.audioAssetIds.has(item.assetId)))),
    );
    if (hasPotentialAudio) {
      const audioSupported = await media.canEncodeAudio("aac", {
        numberOfChannels: AUDIO_CHANNELS,
        sampleRate: AUDIO_SAMPLE_RATE,
        bitrate: 192_000,
      });
      if (!audioSupported) throw new Error("This browser cannot encode AAC audio.");
    }

    const mixedAudio = await mixProjectAudio(renderProject, assets, blobs, resources, options);
    reportProgress(options.onProgress, AUDIO_PROGRESS);
    throwIfAborted(options.signal);

    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("Unable to create the export canvas.");

    const target = new media.BufferTarget();
    output = new media.Output({
      format: new media.Mp4OutputFormat(),
      target,
    });
    videoSource = new media.CanvasSource(canvas, {
      codec: "avc",
      bitrate: media.QUALITY_HIGH,
      keyFrameInterval: 2,
      latencyMode: "quality",
    });
    output.addVideoTrack(videoSource, { frameRate: fps });

    if (mixedAudio) {
      audioSource = new media.AudioBufferSource({ codec: "aac", bitrate: 192_000 });
      output.addAudioTrack(audioSource);
    }

    await output.start();
    const audioPromise = mixedAudio && audioSource ? audioSource.add(mixedAudio) : Promise.resolve();
    const frameCount = Math.ceil(renderProject.duration * fps);
    const frameDuration = 1 / fps;

    for (let frame = 0; frame < frameCount; frame += 1) {
      throwIfAborted(options.signal);
      const timestamp = frame / fps;
      const duration = Math.min(frameDuration, renderProject.duration - timestamp);
      await drawProjectFrame(context, renderProject, resources, timestamp);
      await videoSource.add(timestamp, duration);
      reportProgress(
        options.onProgress,
        AUDIO_PROGRESS + (FRAME_PROGRESS - AUDIO_PROGRESS) * ((frame + 1) / frameCount),
      );
    }

    videoSource.close();
    await audioPromise;
    audioSource?.close();
    throwIfAborted(options.signal);
    await output.finalize();
    if (!target.buffer) throw new Error("The browser encoder produced an empty export.");

    reportProgress(options.onProgress, 1);
    return new Blob([target.buffer], { type: "video/mp4" });
  } catch (error) {
    if (output && output.state !== "canceled" && output.state !== "finalized") {
      await output.cancel().catch(() => undefined);
    }
    if (options.signal?.aborted || isAbortError(error)) throw createAbortError();
    throw error;
  } finally {
    options.signal?.removeEventListener("abort", abortListener);
    await closeRenderResources(resources);
    disposeInputs();
  }
}

/** Starts a browser download for a generated export blob. */
export function downloadBlob(blob: Blob, filename = "supoclip-export.mp4"): void {
  if (typeof document === "undefined") {
    throw new Error("Downloads are only available in a browser.");
  }

  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}
