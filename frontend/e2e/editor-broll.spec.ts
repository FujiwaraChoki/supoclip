import { expect, test, type Page, type Route } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import type { BRollClip, BRollFootage, EditDocument } from "../src/lib/editor/document";
import { draft } from "../src/lib/editor/document.test-fixture";

// A red clip and blue stock footage, so frames show which one is on screen.
const cleanPath = path.resolve("test-results/broll-clean.mp4");
const footagePath = path.resolve("test-results/broll-footage.mp4");
const footage: BRollFootage = {
  provider: "pexels",
  id: 42,
  width: 360,
  height: 640,
  duration: 4,
  fileUrl: "https://videos.pexels.com/video-files/42/42.mp4",
  pageUrl: "https://www.pexels.com/video/42/",
  thumbnail: "",
  credit: "Some Filmmaker",
};
const generated: BRollClip = { id: "gen", start: 1, end: 2, offset: 0, query: "mountain summit", footage };

test.beforeAll(() => {
  mkdirSync(path.dirname(cleanPath), { recursive: true });
  for (const [file, color, size, extra] of [
    [cleanPath, "red", "640x360", ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3", "-c:a", "aac"]],
    [footagePath, "blue", "360x640", []],
  ] as const) {
    execFileSync("ffmpeg", [
      "-v", "error", "-y",
      "-f", "lavfi", "-i", `color=c=${color}:s=${size}:r=24:d=${file === cleanPath ? 3 : 4}`,
      ...extra,
      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", file,
    ]);
  }
});

function serve(route: Route, file: string) {
  const bytes = readFileSync(file),
    range = route.request().headers()["range"]?.match(/bytes=(\d+)-(\d*)/);
  const start = range ? Number(range[1]) : 0,
    end = range && range[2] ? Number(range[2]) : bytes.length - 1;
  return route.fulfill({
    status: range ? 206 : 200,
    body: bytes.subarray(start, end + 1),
    contentType: "video/mp4",
    headers: {
      "Accept-Ranges": "bytes",
      "Content-Length": String(end - start + 1),
      ...(range ? { "Content-Range": `bytes ${start}-${end}/${bytes.length}` } : {}),
    },
  });
}

async function setup(page: Page, options: { document?: EditDocument; legacyCache?: boolean } = {}) {
  const initial = options.document ?? {
    ...structuredClone(draft), framing: { ...draft.framing, aspect: "original" as const }, broll: [generated],
  };
  let saved: { revision: number; document: EditDocument } = {
    revision: 0,
    document: initial,
  };
  if (options.legacyCache) await page.addInitScript((document) => {
    const legacy = { ...document };
    delete legacy.broll;
    localStorage.setItem("supoclip-editor:/api/tasks/one/clips/clip1/editor", JSON.stringify({
      basis: "clip1.mp4", revision: 0, saved: JSON.stringify(legacy),
      history: { past: [], present: legacy, future: [] },
    }));
  }, initial);
  await page.route("**/api/auth/get-session**", (r) =>
    r.fulfill({
      json: {
        session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" },
        user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true },
      },
    }),
  );
  await page.route("**/api/broll/status", (r) => r.fulfill({ json: { configured: true, provider: "pexels" } }));
  await page.route("**/api/tasks/one", (r) =>
    r.fulfill({ json: { id: "one", user_id: "user", source_title: "Talk", status: "completed", source_type: "upload", clips_count: 1 } }),
  );
  await page.route("**/api/tasks/one/clips", (r) =>
    r.fulfill({ json: { clips: [{ id: "clip1", filename: "clip1.mp4", clip_order: 1, duration: 3, text: "Hello world today", video_url: "/x" }] } }),
  );
  await page.route("**/api/fonts/*", (r) =>
    r.fulfill({
      body: readFileSync(path.resolve("../backend/fonts", path.basename(new URL(r.request().url()).pathname) + ".ttf")),
      contentType: "font/ttf",
    }),
  );
  await page.route(/\/api\/tasks\/one\/clips\/clip1\/editor$/, async (r) => {
    if (r.request().method() === "PATCH") {
      const body = r.request().postDataJSON();
      saved = { revision: saved.revision + 1, document: body.document };
      return r.fulfill({ json: { revision: saved.revision } });
    }
    return r.fulfill({
      json: {
        status: "ready", basis: "clip1.mp4", duration: 3, width: 640, height: 360, fps: 24, hasAudio: true,
        waveform: Array.from({ length: 160 }, () => 0.4), draft: saved, original: initial, jobs: [],
      },
    });
  });
  await page.route("**/editor/media/clean.mp4", (r) => serve(r, cleanPath));
  await page.route("**/editor/media/thumbnails.jpg", (r) => r.fulfill({ status: 404 }));
  await page.route("**/editor/broll/footage/42/360x640.mp4", (r) => serve(r, footagePath));
  await page.goto("/tasks/one/edit?clip=clip1");
  await expect(page.getByLabel("Word 1", { exact: true })).toBeVisible();
  return { saved: () => saved };
}

/** Average colour of the preview canvas. */
const canvasColor = (page: Page) =>
  page.getByLabel("Edited video preview").evaluate((canvas: HTMLCanvasElement) => {
    const { data } = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height);
    let r = 0, b = 0;
    for (let i = 0; i < data.length; i += 4) {
      r += data[i];
      b += data[i + 2];
    }
    const n = data.length / 4;
    return { red: r / n, blue: b / n };
  });

async function seekTo(page: Page, seconds: number) {
  await page.locator("#studio-playhead").evaluate((input: HTMLInputElement, value) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(input, String(value));
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }, seconds);
}

test("generated B-roll shows on the timeline and in the preview at its time", async ({ page }) => {
  await setup(page);
  const lane = page.getByRole("group", { name: "B-roll track" });
  await expect(lane.getByRole("button", { name: /B-roll mountain summit/ })).toBeVisible();

  await seekTo(page, 1.5);
  await expect.poll(async () => (await canvasColor(page)).blue, { timeout: 15000 }).toBeGreaterThan(150);
  await seekTo(page, 0.4);
  await expect.poll(async () => (await canvasColor(page)).red, { timeout: 15000 }).toBeGreaterThan(150);
  await page.screenshot({ path: "test-results/editor-broll-preview.png", fullPage: true });
});

test("live playback paints the source, B-roll and source again as the clock advances", async ({ page }) => {
  await setup(page);
  const source = page.locator('video[src$="/media/clean.mp4"]');
  await expect.poll(async () => (await canvasColor(page)).red).toBeGreaterThan(150);
  await page.getByRole("button", { name: "Loop selected edit", exact: true }).click();
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect.poll(async () => (await canvasColor(page)).blue, { intervals: [50] }).toBeGreaterThan(200);
  expect(await source.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(1);
  await expect.poll(() => source.evaluate((v: HTMLVideoElement) => v.currentTime), { intervals: [50] }).toBeGreaterThan(2.4);
  await expect.poll(async () => (await canvasColor(page)).red).toBeGreaterThan(150);
  await expect(page.getByRole("button", { name: "Play", exact: true })).toBeVisible();
  expect(await source.evaluate((v: HTMLVideoElement) => v.getVideoPlaybackQuality().totalVideoFrames)).toBeGreaterThan(40);
  expect(await source.evaluate((v: HTMLVideoElement) => v.error?.message ?? null)).toBeNull();
});

test("footage is searched, added at the playhead, replaced and removed", async ({ page }) => {
  const { saved } = await setup(page);
  const searches: string[] = [];
  await page.route("**/editor/broll/search**", (r) => {
    searches.push(new URL(r.request().url()).searchParams.get("q") ?? "");
    return r.fulfill({ json: { results: [{ ...footage, id: 42 }], provider: "pexels" } });
  });
  await seekTo(page, 2.2);
  await page.getByRole("tab", { name: "B-roll" }).click();
  await page.getByLabel("Search stock footage").fill("coffee pour");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("listitem", { name: /Add footage by Some Filmmaker/ }).click();

  await expect(page.getByRole("button", { name: /B-roll coffee pour/ })).toBeVisible();
  await expect.poll(() => saved().document.broll?.length).toBe(2);
  const added = saved().document.broll!.find((b) => b.query === "coffee pour")!;
  expect(added.start).toBeCloseTo(2.2, 1);
  expect(added.end).toBeCloseTo(3, 1); // limited by the clip's end
  expect(searches).toEqual(["coffee pour"]);
  await page.screenshot({ path: "test-results/editor-broll-panel.png", fullPage: true });

  await page.getByRole("button", { name: "Remove", exact: true }).click();
  await expect.poll(() => saved().document.broll?.length).toBe(1);
});

test("auto-fill adds AI suggestions and the lane drags them", async ({ page }) => {
  const { saved } = await setup(page);
  await page.route("**/editor/broll/suggest", (r) =>
    r.fulfill({ json: { broll: [{ ...generated, id: "ai", start: 2.2, end: 2.9, query: "city at night" }] } }),
  );
  await page.getByRole("tab", { name: "B-roll" }).click();
  await page.getByRole("button", { name: "Auto-fill" }).click();
  await expect(page.getByRole("button", { name: /B-roll city at night/ })).toBeVisible();
  await expect.poll(() => saved().document.broll?.length).toBe(2);

  // Drag the generated clip 0.3 s later. It moves as a whole and stops
  // against the suggested clip at 2.2 s instead of overlapping it.
  const block = page.getByRole("button", { name: /B-roll mountain summit/ });
  await block.scrollIntoViewIfNeeded();
  const box = (await block.boundingBox())!;
  const laneBox = (await page.getByRole("group", { name: "B-roll track" }).boundingBox())!;
  const step = (laneBox.width / 3) * 0.3;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + step, box.y + box.height / 2, { steps: 5 });
  await page.mouse.up();
  await expect
    .poll(() => saved().document.broll?.find((b) => b.id === "gen")?.start)
    .toBeCloseTo(1.2, 2);
  const moved = saved().document.broll!.find((b) => b.id === "gen")!;
  expect(moved.end).toBeCloseTo(2.2, 2);
});

test("a legacy cached draft keeps generated B-roll through editing and undo", async ({ page }) => {
  const { saved } = await setup(page, { legacyCache: true });
  await expect(page.getByRole("button", { name: /B-roll mountain summit/ })).toBeVisible();
  await page.getByLabel("Word 1", { exact: true }).fill("Welcome");
  await expect.poll(() => saved().document.words[0].text).toBe("Welcome");
  expect(saved().document.broll).toEqual([generated]);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect.poll(() => saved().document.words[0].text).toBe("Hello");
  expect(saved().document.broll).toEqual([generated]);
});

for (const mode of ["move", "trim"] as const) {
  test(`B-roll ${mode} retains capture and ends when released outside its lane`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const { saved } = await setup(page);
    const block = page.getByRole("button", { name: /B-roll mountain summit/ });
    const element = (await block.elementHandle())!;
    const box = (await block.boundingBox())!;
    const lane = (await page.getByRole("group", { name: "B-roll track" }).boundingBox())!;
    const delta = lane.width * 0.15 / 3;
    const x = mode === "trim" ? box.x + 3 : box.x + box.width / 2;
    const y = box.y + box.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + delta, y, { steps: 5 });
    await expect(page.getByLabel("B-roll start, seconds", { exact: true })).toHaveValue("1.15");
    expect(await element.evaluate((el) => el.isConnected)).toBe(true);
    // Leave vertically, release, and return with no mouse button held.
    await page.mouse.move(x + delta, box.y - 15);
    await page.mouse.up();
    await expect.poll(() => saved().document.broll![0].start).toBeCloseTo(1.15, 2);
    await page.mouse.move(x + delta * 2, y, { steps: 5 });
    await page.getByRole("tab", { name: "Audio", exact: true }).click();
    await page.getByRole("switch", { name: "Mute export audio", exact: true }).click();
    // Another autosave proves hovering did not change the cutaway.
    await expect.poll(() => saved().document.muted).toBe(true);
    expect(saved().document.broll![0].start).toBeCloseTo(1.15, 2);
    expect(saved().document.broll![0].end).toBeCloseTo(mode === "trim" ? 2 : 2.15, 2);
  });
}

for (const reordered of [false, true]) test(`B-roll drag crosses ${reordered ? "reordered" : "removed"} source time without losing capture, undo or export`, async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const cuts = [{ id: "one", start: 0, end: 1 }, { id: "two", start: 2, end: 3 }];
  const initial: EditDocument = { ...structuredClone(draft), segments: reordered ? [...cuts].reverse() : cuts,
    framing: { ...draft.framing, aspect: "original" }, broll: [{ ...generated, start: reordered ? 2.3 : .3, end: reordered ? 2.7 : .7 }] };
  const { saved } = await setup(page, { document: initial });
  const lane = page.getByRole("group", { name: "B-roll track" });
  const block = lane.getByRole("button", { name: /B-roll mountain summit/ });
  const box = (await block.boundingBox())!, track = (await lane.boundingBox())!;
  const x = box.x + box.width / 2, y = box.y + box.height / 2, step = track.width / 2;
  await page.mouse.move(x, y); await page.mouse.down();
  // The block is replaced when its destination switches to a different segment.
  await page.mouse.move(x + step * .8, y, { steps: 10 });
  await page.mouse.move(x + step, y, { steps: 5 });
  await page.mouse.move(x + step, box.y - 20); await page.mouse.up();
  const expected = reordered ? .3 : 2.3;
  await expect.poll(() => saved().document.broll![0].start).toBeCloseTo(expected, 2);
  expect(saved().document.broll![0].end).toBeCloseTo(expected + .4, 2);
  await expect(block).toBeVisible();
  await seekTo(page, 1.5);
  await expect.poll(async () => (await canvasColor(page)).blue).toBeGreaterThan(200);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect.poll(() => saved().document.broll![0].start).toBeCloseTo(initial.broll![0].start, 2);
  await expect(page.getByRole("button", { name: "Undo", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await expect.poll(() => saved().document.broll![0].start).toBeCloseTo(expected, 2);
  await page.reload();
  await expect(block).toBeVisible();
  if (!reordered) {
    await page.getByRole("button", { name: "Export", exact: true }).click();
    await page.getByRole("button", { name: /On this device/ }).click();
    const download = page.waitForEvent("download", { timeout: 60000 });
    await page.getByRole("button", { name: "Export clip", exact: true }).click();
    const file = info.outputPath("cross-cut-broll.mp4"); await (await download).saveAs(file);
    const pixel = (time: number) => execFileSync("ffmpeg", ["-v", "error", "-ss", String(time), "-i", file, "-frames:v", "1", "-vf", "crop=40:40:0:150,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]);
    expect(pixel(1.5)[2]).toBeGreaterThan(200);
    expect(pixel(.5)[0]).toBeGreaterThan(150);
  }
});

const exportCases: { name: string; broll: BRollClip[]; at: number[]; segments?: EditDocument["segments"]; blue?: [number, number] }[] = [
  { name: "generated", broll: [generated], at: [1.5] },
  { name: "minimum-length", broll: [{ ...generated, end: 1.2 }], at: [1.0834] },
  { name: "combined continuation", broll: [
    { ...generated, end: 1.5, fadeOut: false, fadeDuration: 0.25 },
    { ...generated, id: "continuation", start: 1.5, offset: 0.5, fadeIn: false, fadeDuration: 0.25 },
  ], at: [1.4167, 1.5, 1.5834] },
  { name: "splits inside both dissolves", broll: [generated], at: [1.125, 1.875], blue: [100, 155],
    segments: [0, 1.02, 1.083333, 1.916667, 1.98].map((start, index, starts) => ({
      id: `split-${index}`, start, end: starts[index + 1] ?? 3,
    })),
  },
];

for (const { name, broll, at, segments, blue = [200, 256] } of exportCases) {
  test(`preview and browser export keep ${name} B-roll under the captions`, async ({ page }, info) => {
    await setup(page, { document: { ...structuredClone(draft), ...(segments ? { segments } : {}), framing: { ...draft.framing, aspect: "original" }, broll } });
    for (const time of at) {
      await seekTo(page, time);
      await expect.poll(async () => (await canvasColor(page)).blue).toBeGreaterThan(blue[0] - 10);
      await expect.poll(async () => (await canvasColor(page)).blue).toBeLessThan(blue[1]);
    }
    await page.getByRole("button", { name: "Export", exact: true }).click();
    await page.getByRole("button", { name: /On this device/ }).click();
    const downloadPromise = page.waitForEvent("download", { timeout: 60000 });
    await page.getByRole("button", { name: "Export clip", exact: true }).click();
    const file = info.outputPath("broll-export.mp4");
    await (await downloadPromise).saveAs(file);

    const pixel = (at: number) => {
      const raw = execFileSync("ffmpeg", [
        "-v", "error", "-ss", String(at), "-i", file, "-frames:v", "1",
        "-vf", "crop=40:40:0:150,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
      ]);
      return { red: raw[0], blue: raw[2] };
    };
    expect(pixel(0.4).red).toBeGreaterThan(150);
    for (const time of at) {
      expect(pixel(time).blue).toBeGreaterThan(blue[0]);
      expect(pixel(time).blue).toBeLessThan(blue[1]);
    }
    expect(pixel(2.6).red).toBeGreaterThan(150);
  });
}

test("browser export matches the independently moved and resized preview text", async ({ page }, info) => {
  await setup(page, { document: {
    ...structuredClone(draft), broll: [], hookTitle: "TITLE",
    framing: { ...draft.framing, aspect: "original" },
    words: [{ id: "phrase", start: 0, end: 3, text: "CAPTION", highlight: false }],
    captions: { ...draft.captions, template: "minimal", background: false, color: "#FFFFFF", accent: "#FFFFFF" },
    textTransforms: { hook: { x: .15, y: .1, scale: 1.5 }, captions: { x: -.15, y: -.15, scale: 1.5 } },
  } });
  await seekTo(page, .5);
  const preview = page.getByRole("region", { name: "Video preview", exact: true });
  const layerBox = (index: number) => preview.locator("canvas").nth(index).evaluate((canvas: HTMLCanvasElement) => {
    const pixels = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height).data;
    let x1 = canvas.width, y1 = canvas.height, x2 = -1, y2 = -1;
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i + 3] > 180 && pixels[i] > 180 && pixels[i + 1] > 180 && pixels[i + 2] > 180) {
      const x = i / 4 % canvas.width, y = Math.floor(i / 4 / canvas.width);
      x1 = Math.min(x1, x); y1 = Math.min(y1, y); x2 = Math.max(x2, x); y2 = Math.max(y2, y);
    }
    return { x: (x1 + x2) / 2 / canvas.width, y: (y1 + y2) / 2 / canvas.height, width: (x2 - x1 + 1) / canvas.width };
  });
  await expect.poll(async () => (await layerBox(1)).width).toBeGreaterThan(.05);
  const title = await layerBox(1), caption = await layerBox(2);
  await page.getByRole("button", { name: "Export", exact: true }).click();
  await page.getByRole("button", { name: /On this device/ }).click();
  const download = page.waitForEvent("download", { timeout: 60000 });
  await page.getByRole("button", { name: "Export clip", exact: true }).click();
  const file = info.outputPath("transformed-text.mp4");
  await (await download).saveAs(file);
  const raw = execFileSync("ffmpeg", ["-v", "error", "-ss", "0.5", "-i", file, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]);
  for (const [expected, from, to] of [[title, 0, 180], [caption, 180, 360]] as const) {
    let x1 = 640, y1 = 360, x2 = -1, y2 = -1;
    for (let y = from; y < to; y++) for (let x = 0; x < 640; x++) {
      const i = (y * 640 + x) * 3;
      if (raw[i] > 180 && raw[i + 1] > 180 && raw[i + 2] > 180) {
        x1 = Math.min(x1, x); y1 = Math.min(y1, y); x2 = Math.max(x2, x); y2 = Math.max(y2, y);
      }
    }
    expect((x1 + x2) / 2 / 640).toBeCloseTo(expected.x, 2);
    expect((y1 + y2) / 2 / 360).toBeCloseTo(expected.y, 2);
    expect((x2 - x1 + 1) / 640).toBeCloseTo(expected.width, 2);
  }
});

test("browser export keeps individual caption placements separate", async ({ page }, info) => {
  await setup(page, { document: {
    ...structuredClone(draft), broll: [], framing: { ...draft.framing, aspect: "original" },
    captions: { ...draft.captions, wordsPerLine: 1, template: "minimal", background: false, color: "#FFFFFF", accent: "#FFFFFF" },
    words: [{ id: "first", start: 0, end: 1.5, text: "CAPTION", highlight: false }, { id: "second", start: 1.5, end: 3, text: "CAPTION", highlight: false }],
    captionTransforms: { '["s","first"]': { x: .15, y: -.2, scale: 1.5 } },
  } });
  const raster = () => page.getByRole("region", { name: "Video preview", exact: true }).locator("canvas").nth(2).evaluate((canvas: HTMLCanvasElement) => {
    const pixels = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height).data;
    let x1 = canvas.width, y1 = canvas.height, x2 = -1, y2 = -1;
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i + 3] > 180 && pixels[i] > 180 && pixels[i + 1] > 180 && pixels[i + 2] > 180) {
      const x = i / 4 % canvas.width, y = Math.floor(i / 4 / canvas.width);
      x1 = Math.min(x1, x); y1 = Math.min(y1, y); x2 = Math.max(x2, x); y2 = Math.max(y2, y);
    }
    return { x: (x1 + x2) / 2 / canvas.width * 640, y: (y1 + y2) / 2 / canvas.height * 360, width: (x2 - x1) / canvas.width * 640 };
  });
  await seekTo(page, .5);
  await expect.poll(async () => (await raster()).x / 640).toBeCloseTo(.65, 2);
  const firstPreview = await raster();
  await seekTo(page, 2);
  await expect.poll(async () => (await raster()).x / 640).toBeCloseTo(.5, 2);
  const secondPreview = await raster();
  await page.getByRole("button", { name: "Export", exact: true }).click();
  await page.getByRole("button", { name: /On this device/ }).click();
  const download = page.waitForEvent("download", { timeout: 60000 });
  await page.getByRole("button", { name: "Export clip", exact: true }).click();
  const file = info.outputPath("individual-captions.mp4");
  await (await download).saveAs(file);
  const bounds = (time: number) => {
    const raw = execFileSync("ffmpeg", ["-v", "error", "-ss", String(time), "-i", file, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]);
    let x1 = 640, y1 = 360, x2 = -1, y2 = -1;
    for (let y = 0; y < 360; y++) for (let x = 0; x < 640; x++) {
      const i = (y * 640 + x) * 3;
      if (raw[i] > 180 && raw[i + 1] > 180 && raw[i + 2] > 180) { x1 = Math.min(x1, x); y1 = Math.min(y1, y); x2 = Math.max(x2, x); y2 = Math.max(y2, y); }
    }
    return { x: (x1 + x2) / 2, y: (y1 + y2) / 2, width: x2 - x1 };
  };
  const first = bounds(.5), second = bounds(2);
  // Compare rendered glyphs with the preview rather than their font baselines.
  for (const [actual, expected] of [[first, firstPreview], [second, secondPreview]]) {
    expect(Math.abs(actual.x - expected.x)).toBeLessThanOrEqual(2);
    expect(Math.abs(actual.y - expected.y)).toBeLessThanOrEqual(2);
    expect(Math.abs(actual.width - expected.width)).toBeLessThanOrEqual(3);
  }
  expect(first.width / second.width).toBeCloseTo(1.5, 1);
});

test("dragging a B-roll continuation across a contiguous split keeps its length and position", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const initial: EditDocument = { ...structuredClone(draft), segments: [{ id: "head", start: 0, end: 1.5 }, { id: "tail", start: 1.5, end: 3 }],
    broll: [{ ...generated, start: .5, end: 2.5 }] };
  const { saved } = await setup(page, { document: initial });
  const lane = page.getByRole("group", { name: "B-roll track" }), track = (await lane.boundingBox())!;
  const continuation = lane.getByRole("button", { name: /B-roll mountain summit/ }).nth(1), box = (await continuation.boundingBox())!;
  const x = box.x + box.width / 2, y = box.y + box.height / 2;
  await page.mouse.move(x, y); await page.mouse.down();
  await page.mouse.move(x - track.width * .2 / 3, y, { steps: 12 }); await page.mouse.up();
  await expect.poll(() => saved().document.broll![0].start).toBeCloseTo(.3, 2);
  expect(saved().document.broll![0].end).toBeCloseTo(2.3, 2);
  await expect(continuation).toBeVisible();
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect.poll(() => saved().document.broll![0].start).toBeCloseTo(.5, 2);
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await expect.poll(() => saved().document.broll![0].start).toBeCloseTo(.3, 2);
  await page.reload(); await expect(continuation).toBeVisible();
  expect(saved().document.broll![0].end - saved().document.broll![0].start).toBeCloseTo(2, 6);
});
