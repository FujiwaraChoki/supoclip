import { describe, expect, it } from "vitest";

import {
  activeItemsAtTime,
  clampItemToProject,
  createInitialProject,
  duplicateItem,
  formatTime,
  itemLocalTime,
  loadCompatibleProject,
  normalizeProject,
  projectDuration,
  reorderItem,
  splitItem,
} from "./editor-utils";
import type { Asset, TimelineItem } from "./types";

const videoAsset: Asset = {
  id: "asset-1",
  name: "Interview",
  kind: "video",
  source: "generated",
  url: "/api/tasks/task-1/clips/clip-1/file",
  duration: 12,
  width: 1080,
  height: 1920,
};

function item(overrides: Partial<TimelineItem> = {}): TimelineItem {
  return {
    id: "item-1",
    assetId: "asset-1",
    type: "video",
    name: "Interview",
    track: "main",
    start: 2,
    duration: 8,
    trimStart: 3,
    speed: 1,
    volume: 100,
    muted: false,
    hidden: false,
    locked: false,
    opacity: 100,
    blendMode: "normal",
    transform: { x: 0, y: 0, width: 1080, height: 1920, rotation: 0 },
    crop: { top: 0, right: 0, bottom: 0, left: 0 },
    effects: { brightness: 100, contrast: 100, saturation: 100, blur: 0, hue: 0 },
    fadeIn: 0,
    fadeOut: 0,
    ...overrides,
  };
}

describe("editor project helpers", () => {
  it("creates a vertical project with the first asset on the main track", () => {
    const project = createInitialProject("task-1", "Launch edit", videoAsset);

    expect(project).toMatchObject({
      schemaVersion: 1,
      name: "Launch edit",
      taskId: "task-1",
      version: 1,
      canvas: { width: 1080, height: 1920, background: "#000000", fps: 30 },
      duration: 12,
    });
    expect(project.items).toHaveLength(1);
    expect(project.items[0]).toMatchObject({
      assetId: "asset-1",
      type: "video",
      track: "main",
      duration: 12,
    });
  });

  it("normalizes unsafe numeric values and expands duration to fit items", () => {
    const project = createInitialProject("task-1", "", videoAsset);
    project.canvas.width = -1;
    project.canvas.height = 1921;
    project.canvas.fps = 500;
    project.duration = 1;
    project.items[0] = item({
      start: -2,
      duration: 5,
      speed: 0,
      volume: 9,
      opacity: -1,
      fadeIn: 4,
      fadeOut: 4,
      crop: { top: 80, right: 80, bottom: 80, left: 80 },
    });

    const normalized = normalizeProject(project);

    expect(normalized.name).toBe("Untitled project");
    expect(normalized.canvas.width).toBe(1080);
    expect(normalized.canvas.height).toBe(1922);
    expect(normalized.canvas.fps).toBe(120);
    expect(normalized.items[0]).toMatchObject({
      start: 0,
      duration: 5,
      speed: 0.25,
      volume: 9,
      opacity: 0,
      fadeIn: 2.5,
      fadeOut: 2.5,
    });
    expect(normalized.items[0].crop.left + normalized.items[0].crop.right).toBeCloseTo(99.9);
    expect(normalized.duration).toBe(5);
  });

  it("keeps an intentional project tail while including later items", () => {
    expect(projectDuration({ duration: 20, items: [item()] })).toBe(20);
    expect(projectDuration({ duration: 4, items: [item({ start: 9, duration: 3 })] })).toBe(12);
    expect(projectDuration([item({ start: 1, duration: 2 })])).toBe(3);
  });

  it("never replaces an incompatible saved project with a blank draft", () => {
    expect(() => loadCompatibleProject({ schemaVersion: 99 }, "task-1", "Draft", videoAsset))
      .toThrow(/left untouched/);

    const fresh = loadCompatibleProject(null, "task-1", "Draft", videoAsset);
    expect(fresh.items[0]?.assetId).toBe(videoAsset.id);
  });
});

describe("timeline editing helpers", () => {
  it("splits in timeline time and advances the second source trim by speed", () => {
    const source = item({ speed: 1.5, fadeIn: 1, fadeOut: 2 });
    const [first, second] = splitItem(source, 5);

    expect(first).toMatchObject({ id: "item-1", start: 2, duration: 3, trimStart: 3, fadeIn: 1, fadeOut: 0 });
    expect(second).toMatchObject({ start: 5, duration: 5, trimStart: 7.5, fadeIn: 0, fadeOut: 2 });
    expect(second.id).not.toBe(first.id);
    expect(() => splitItem(source, source.start)).toThrow(RangeError);
  });

  it("duplicates nested item state without sharing references", () => {
    const source = item({ text: {
      content: "Hello",
      fontFamily: "Inter",
      fontSize: 48,
      fontWeight: 700,
      color: "#ffffff",
      backgroundColor: "#000000",
      align: "center",
      letterSpacing: 0,
      lineHeight: 1.2,
      strokeColor: "#000000",
      strokeWidth: 0,
    } });
    const duplicate = duplicateItem(source, 1.5);

    expect(duplicate).toMatchObject({ name: "Interview copy", start: 3.5 });
    expect(duplicate.id).not.toBe(source.id);
    expect(duplicate.transform).not.toBe(source.transform);
    expect(duplicate.text).not.toBe(source.text);
  });

  it("clamps an item to the declared project boundary", () => {
    expect(clampItemToProject(item({ start: 8, duration: 8 }), 10)).toMatchObject({
      start: 8,
      duration: 2,
    });
    expect(clampItemToProject(item({ start: 20 }), 10)).toMatchObject({
      start: 10,
      duration: 0,
    });
  });

  it("uses item array order as back-to-front z-order", () => {
    const back = item({ id: "back", track: "main", start: 0 });
    const hidden = item({ id: "hidden", track: "overlay", start: 0, hidden: true });
    const front = item({ id: "front", track: "text", start: 0 });
    const reordered = reorderItem([back, hidden, front], "back", 2);

    expect(reordered.map(({ id }) => id)).toEqual(["hidden", "front", "back"]);
    expect(activeItemsAtTime(reordered, 3).map(({ id }) => id)).toEqual(["front", "back"]);
    expect(activeItemsAtTime(reordered, 10)).toEqual([]);
  });

  it("maps timeline time to trimmed, speed-adjusted source time", () => {
    const source = item({ start: 2, duration: 4, trimStart: 10, speed: 2 });

    expect(itemLocalTime(source, 1)).toBe(10);
    expect(itemLocalTime(source, 4)).toBe(14);
    expect(itemLocalTime(source, 99)).toBe(18);
  });
});

describe("formatTime", () => {
  it("formats short, precise, and hour-long timeline values", () => {
    expect(formatTime(65.432)).toBe("01:05");
    expect(formatTime(65.432, 2)).toBe("01:05.43");
    expect(formatTime(3661.9)).toBe("01:01:01");
    expect(formatTime(Number.NaN)).toBe("00:00");
  });
});
