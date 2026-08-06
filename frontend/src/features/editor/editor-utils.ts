import {
  EDITOR_SCHEMA_VERSION,
  type Asset,
  type Project,
  type TimelineCrop,
  type TimelineItem,
  type TimelineText,
} from "./types";

export const DEFAULT_EDITOR_CANVAS = {
  width: 1080,
  height: 1920,
  background: "#000000",
  fps: 30,
} as const;

const MIN_SPEED = 0.25;
const MAX_SPEED = 4;

function editorId(prefix: "project" | "item"): string {
  const id = globalThis.crypto?.randomUUID?.();
  if (id) return `${prefix}-${id}`;

  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function finiteOr(value: number, fallback: number): number {
  return Number.isFinite(value) ? value : fallback;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

function positiveOr(value: number, fallback: number): number {
  return value > 0 && Number.isFinite(value) ? value : fallback;
}

function evenDimension(value: number, fallback: number): number {
  return Math.max(2, Math.round(positiveOr(value, fallback) / 2) * 2);
}

function cloneItem(item: TimelineItem): TimelineItem {
  return {
    ...item,
    transform: { ...item.transform },
    crop: { ...item.crop },
    effects: { ...item.effects },
    text: item.text ? { ...item.text } : undefined,
    shape: item.shape ? { ...item.shape } : undefined,
  };
}

function normalizeCrop(crop: TimelineCrop): TimelineCrop {
  let top = clamp(finiteOr(crop.top, 0), 0, 100);
  let right = clamp(finiteOr(crop.right, 0), 0, 100);
  let bottom = clamp(finiteOr(crop.bottom, 0), 0, 100);
  let left = clamp(finiteOr(crop.left, 0), 0, 100);

  const horizontal = left + right;
  if (horizontal >= 100) {
    const scale = 99.9 / horizontal;
    left *= scale;
    right *= scale;
  }

  const vertical = top + bottom;
  if (vertical >= 100) {
    const scale = 99.9 / vertical;
    top *= scale;
    bottom *= scale;
  }

  return { top, right, bottom, left };
}

function normalizeText(text: TimelineText): TimelineText {
  return {
    ...text,
    content: text.content ?? "",
    fontFamily: text.fontFamily || "Inter",
    fontSize: positiveOr(text.fontSize, 48),
    fontWeight: clamp(Math.round(finiteOr(text.fontWeight, 700)), 100, 900),
    letterSpacing: finiteOr(text.letterSpacing, 0),
    lineHeight: positiveOr(text.lineHeight, 1.2),
    strokeWidth: Math.max(0, finiteOr(text.strokeWidth, 0)),
  };
}

export function normalizeTimelineItem(item: TimelineItem): TimelineItem {
  const duration = Math.max(0, finiteOr(item.duration, 0));
  let fadeIn = clamp(finiteOr(item.fadeIn, 0), 0, duration);
  let fadeOut = clamp(finiteOr(item.fadeOut, 0), 0, duration);
  const combinedFades = fadeIn + fadeOut;

  if (combinedFades > duration && combinedFades > 0) {
    const scale = duration / combinedFades;
    fadeIn *= scale;
    fadeOut *= scale;
  }

  return {
    ...cloneItem(item),
    start: Math.max(0, finiteOr(item.start, 0)),
    duration,
    trimStart: Math.max(0, finiteOr(item.trimStart, 0)),
    speed: clamp(finiteOr(item.speed, 1), MIN_SPEED, MAX_SPEED),
    volume: clamp(finiteOr(item.volume, 100), 0, 100),
    opacity: clamp(finiteOr(item.opacity, 100), 0, 100),
    transform: {
      x: finiteOr(item.transform.x, 0),
      y: finiteOr(item.transform.y, 0),
      width: positiveOr(item.transform.width, 1),
      height: positiveOr(item.transform.height, 1),
      rotation: finiteOr(item.transform.rotation, 0),
    },
    crop: normalizeCrop(item.crop),
    effects: {
      brightness: clamp(finiteOr(item.effects.brightness, 100), 0, 400),
      contrast: clamp(finiteOr(item.effects.contrast, 100), 0, 400),
      saturation: clamp(finiteOr(item.effects.saturation, 100), 0, 400),
      blur: clamp(finiteOr(item.effects.blur, 0), 0, 100),
      hue: clamp(finiteOr(item.effects.hue, 0), -360, 360),
    },
    text: item.text ? normalizeText(item.text) : undefined,
    shape: item.shape
      ? {
          ...item.shape,
          borderRadius: Math.max(0, finiteOr(item.shape.borderRadius, 0)),
        }
      : undefined,
    fadeIn,
    fadeOut,
  };
}

function itemFromAsset(asset: Asset): TimelineItem {
  return {
    id: editorId("item"),
    assetId: asset.id,
    type: asset.kind,
    name: asset.name,
    track: asset.kind === "audio" ? "audio" : "main",
    start: 0,
    duration: Math.max(0, finiteOr(asset.duration, 0)),
    trimStart: 0,
    speed: 1,
    volume: 100,
    muted: false,
    hidden: false,
    locked: false,
    opacity: 100,
    blendMode: "normal",
    transform: {
      x: 50,
      y: 50,
      width: 100,
      height: 100,
      rotation: 0,
    },
    crop: { top: 0, right: 0, bottom: 0, left: 0 },
    effects: {
      brightness: 100,
      contrast: 100,
      saturation: 100,
      blur: 0,
      hue: 0,
    },
    fadeIn: 0,
    fadeOut: 0,
  };
}

export function createInitialProject(
  taskId: string,
  name: string,
  firstAsset?: Asset,
): Project {
  const canvas = { ...DEFAULT_EDITOR_CANVAS };
  const items = firstAsset ? [itemFromAsset(firstAsset)] : [];

  return {
    schemaVersion: EDITOR_SCHEMA_VERSION,
    id: editorId("project"),
    name: name.trim() || "Untitled project",
    taskId,
    version: 1,
    canvas,
    duration: firstAsset ? Math.max(0, finiteOr(firstAsset.duration, 0)) : 0,
    items,
  };
}

export function loadCompatibleProject(
  value: unknown,
  taskId: string,
  name: string,
  firstAsset?: Asset,
): Project {
  if (value === null || value === undefined) {
    return createInitialProject(taskId, name, firstAsset);
  }

  const candidate = value as Partial<Project>;
  if (
    candidate.schemaVersion !== EDITOR_SCHEMA_VERSION
    || typeof candidate.id !== "string"
    || typeof candidate.name !== "string"
    || candidate.taskId !== taskId
    || !candidate.canvas
    || !Array.isArray(candidate.items)
    || candidate.items.some((item) => !item?.transform || !item.crop || !item.effects)
  ) {
    throw new Error(
      "This saved editor project is incompatible or damaged. It was left untouched so it can be recovered safely.",
    );
  }

  return normalizeProject(candidate as Project);
}

export function projectDuration(
  projectOrItems:
    | Pick<Project, "duration" | "items">
    | readonly TimelineItem[],
): number {
  const isItems = Array.isArray(projectOrItems);
  const items: readonly TimelineItem[] = isItems
    ? projectOrItems
    : (projectOrItems as Pick<Project, "items">).items;
  const declaredDuration = isItems
    ? 0
    : Math.max(
        0,
        finiteOr(
          (projectOrItems as Pick<Project, "duration">).duration,
          0,
        ),
      );
  return items.reduce(
    (maximum, item) =>
      Math.max(
        maximum,
        Math.max(0, finiteOr(item.start, 0)) +
          Math.max(0, finiteOr(item.duration, 0)),
      ),
    declaredDuration,
  );
}

export function normalizeProject(project: Project): Project {
  const canvas = {
    width: evenDimension(project.canvas.width, DEFAULT_EDITOR_CANVAS.width),
    height: evenDimension(project.canvas.height, DEFAULT_EDITOR_CANVAS.height),
    background: project.canvas.background || DEFAULT_EDITOR_CANVAS.background,
    fps: clamp(
      Math.round(finiteOr(project.canvas.fps, DEFAULT_EDITOR_CANVAS.fps)),
      1,
      120,
    ),
  };
  const normalized: Project = {
    ...project,
    schemaVersion: EDITOR_SCHEMA_VERSION,
    name: project.name.trim() || "Untitled project",
    version: Math.max(1, Math.floor(finiteOr(project.version, 1))),
    canvas,
    duration: Math.max(0, finiteOr(project.duration, 0)),
    items: project.items.map(normalizeTimelineItem),
  };

  return { ...normalized, duration: projectDuration(normalized) };
}

export function splitItem(
  item: TimelineItem,
  timelineTime: number,
): [TimelineItem, TimelineItem] {
  const splitAt = finiteOr(timelineTime, Number.NaN);
  const itemEnd = item.start + item.duration;
  if (!Number.isFinite(splitAt) || splitAt <= item.start || splitAt >= itemEnd) {
    throw new RangeError("Split time must be inside the timeline item");
  }

  const firstDuration = splitAt - item.start;
  const secondDuration = itemEnd - splitAt;
  const first = normalizeTimelineItem({
    ...cloneItem(item),
    duration: firstDuration,
    fadeOut: 0,
  });
  const second = normalizeTimelineItem({
    ...cloneItem(item),
    id: editorId("item"),
    start: splitAt,
    duration: secondDuration,
    trimStart: item.trimStart + firstDuration * item.speed,
    fadeIn: 0,
  });

  return [first, second];
}

export function duplicateItem(
  item: TimelineItem,
  startOffset = 0,
): TimelineItem {
  return normalizeTimelineItem({
    ...cloneItem(item),
    id: editorId("item"),
    name: `${item.name} copy`,
    start: Math.max(0, item.start + finiteOr(startOffset, 0)),
  });
}

export function clampItemToProject(
  item: TimelineItem,
  projectOrDuration: Project | number,
): TimelineItem {
  const maximumDuration = Math.max(
    0,
    finiteOr(
      typeof projectOrDuration === "number"
        ? projectOrDuration
        : projectOrDuration.duration,
      0,
    ),
  );
  const start = clamp(finiteOr(item.start, 0), 0, maximumDuration);

  return normalizeTimelineItem({
    ...cloneItem(item),
    start,
    duration: clamp(finiteOr(item.duration, 0), 0, maximumDuration - start),
  });
}

export function formatTime(
  seconds: number,
  fractionDigitsOrPrecise: number | boolean = 0,
): string {
  const requestedPrecision =
    typeof fractionDigitsOrPrecise === "boolean"
      ? fractionDigitsOrPrecise
        ? 2
        : 0
      : fractionDigitsOrPrecise;
  const precision = clamp(Math.floor(finiteOr(requestedPrecision, 0)), 0, 3);
  const scale = 10 ** precision;
  const totalUnits = Math.floor(Math.max(0, finiteOr(seconds, 0)) * scale);
  const wholeSeconds = Math.floor(totalUnits / scale);
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const remainingSeconds = wholeSeconds % 60;
  const fraction = precision > 0 ? `.${String(totalUnits % scale).padStart(precision, "0")}` : "";
  const secondsPart = `${String(remainingSeconds).padStart(2, "0")}${fraction}`;

  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${secondsPart}`
    : `${String(minutes).padStart(2, "0")}:${secondsPart}`;
}

export function activeItemsAtTime(
  projectOrItems: Project | readonly TimelineItem[],
  timelineTime: number,
): TimelineItem[] {
  const items: readonly TimelineItem[] = Array.isArray(projectOrItems)
    ? projectOrItems
    : (projectOrItems as Project).items;
  const time = Math.max(0, finiteOr(timelineTime, 0));

  // Array order is z-order: the first active item is at the back and the last
  // active item is at the front.
  return items.filter(
    (item) =>
      !item.hidden &&
      time >= item.start &&
      time < item.start + item.duration,
  );
}

export function itemLocalTime(
  item: TimelineItem,
  timelineTime: number,
): number {
  const elapsed = clamp(
    finiteOr(timelineTime, item.start) - item.start,
    0,
    Math.max(0, item.duration),
  );
  return Math.max(0, item.trimStart) + elapsed * Math.max(MIN_SPEED, item.speed);
}

export function reorderItem(
  items: readonly TimelineItem[],
  itemId: string,
  targetIndex: number,
): TimelineItem[] {
  const currentIndex = items.findIndex((item) => item.id === itemId);
  if (currentIndex < 0) return [...items];

  const reordered = [...items];
  const [item] = reordered.splice(currentIndex, 1);
  const nextIndex = clamp(
    Math.floor(finiteOr(targetIndex, currentIndex)),
    0,
    reordered.length,
  );
  reordered.splice(nextIndex, 0, item);
  return reordered;
}
