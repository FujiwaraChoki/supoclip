import { expect, test, type Page, type Route } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import type { DocFx, EditDocument } from "../src/lib/editor/document";
import { draft } from "../src/lib/editor/document.test-fixture";
import type { Beat, FxCue } from "../src/lib/fx/core/types";

// The studio's Dynamic edits: Edits tab → resolve → FX lane, delete + undo, drag, the preview's flash and the export.
const mediaPath = path.resolve("test-results/fx-clean.mp4");
const thumbsPath = path.resolve("test-results/fx-thumbnails.jpg");
test.beforeAll(() => {
  mkdirSync(path.dirname(mediaPath), { recursive: true });
  // A dark, flat picture so a white flash is unmistakable.
  execFileSync("ffmpeg", [
    "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x1c1917:size=1080x1920:rate=30:duration=3",
    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", mediaPath,
  ]);
  execFileSync("ffmpeg", ["-v", "error", "-y", "-i", mediaPath, "-vf", "fps=12/3,scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2,tile=12x1", "-frames:v", "1", thumbsPath]);
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

// == DEFAULT_DOC_FX (lib/editor/document.ts pulls in "@/" imports the Playwright loader can't resolve).
const DEFAULT_DOC_FX: DocFx = {
  version: "fx-v1",
  style: {
    intensity: "off", sfx: true, music: true, music_mood: "auto", zooms: true, shake: true, transitions: true,
    speed: true, censor: "bleep_bar", grade: "auto", grain: false, progress_bar: false, loop_ending: false,
    wait_for_it: true, reactions: false,
  },
  seed: 0, mood: null, sensitivity: "normal", timing: "word", beats: [], cues: [], suppressed: [], disabled: [],
  music: null, grade: "none", grain: 0, progressBar: false, progressColor: "#FF7A1A", loopEnding: "none",
  hookTitleSuppressed: false,
};

const beat: Beat = {
  id: "b1", kind: "emphasis", start: 1, end: 1.5, text: "world", strength: 3, tone: "neutral", emoji: "", label: "",
  value: null, prefix: "", suffix: "", source: "llm", src_start: 1, src_end: 1.5, words: ["world"],
};
const auto = { seed: 7, beat: "b1", origin: "auto" as const };
/** What the mocked resolver returns for an active intensity (S == E: one segment from 0). */
const RESOLVED: FxCue[] = [
  { ...auto, id: "b1.zoom", kind: "zoom", start: 0.2, end: 0.9, mode: "punch", scale: 1.12, anchor: "center" },
  { ...auto, id: "b1.flash", kind: "flash", start: 1, end: 2, color: "#FFFFFF", peak: 1 },
  { ...auto, id: "b1.sfx", kind: "sfx", start: 1, end: 1, sound: "whoosh", variant: 0, gain_db: -2 },
  { ...auto, id: "b1.freeze", kind: "freeze", start: 2.5, end: 2.5, hold: 0.5, label: "" },
];
const planned: DocFx = { ...DEFAULT_DOC_FX, seed: 7, mood: "hype", beats: [beat] };
const initial: EditDocument = { ...structuredClone(draft), fx: planned };

async function setup(page: Page) {
  let saved: { revision: number; document: EditDocument } = { revision: 0, document: structuredClone(initial) };
  const resolves: { document: EditDocument; style: DocFx["style"] }[] = [];
  const exports: { document: EditDocument }[] = [];
  await page.route("**/api/auth/get-session**", (r) =>
    r.fulfill({
      json: {
        session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" },
        user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true },
      },
    }),
  );
  await page.route("**/api/feature-flags", (r) => r.fulfill({ json: { platform: "web", flags: { dynamic_edits: true } } }));
  // No tour in this suite: its card would sit over the studio.
  await page.route("**/api/feature-tours", (r) => r.fulfill({ status: 404, json: {} }));
  await page.route("**/api/tasks/edit-styles", (r) =>
    r.fulfill({
      json: {
        enabled: true, intensities: ["off", "subtle", "bold", "max"],
        defaults: { ...DEFAULT_DOC_FX.style, intensity: undefined },
        music_moods: ["auto", "hype", "chill", "dramatic", "funny", "inspiring", "dark"],
        grades: ["auto", "none", "punchy", "warm", "cool", "mono", "film"], templates: [], reactions_available: false,
      },
    }),
  );
  await page.route("**/api/fx/assets**", (r) => r.fulfill({ status: 404, json: { detail: "not here" } }));
  await page.route("**/api/broll/status", (r) => r.fulfill({ json: { configured: false } }));
  await page.route("**/api/tasks/one", (r) =>
    r.fulfill({ json: { id: "one", user_id: "user", source_title: "Try something new", status: "completed", source_type: "upload", clips_count: 1 } }),
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
      saved = { revision: saved.revision + 1, document: r.request().postDataJSON().document };
      return r.fulfill({ json: { revision: saved.revision } });
    }
    return r.fulfill({
      json: {
        status: "ready", basis: "clip1.mp4", duration: 3, width: 1080, height: 1920, fps: 30, hasAudio: true,
        waveform: Array.from({ length: 160 }, (_, i) => 0.2 + Math.abs(Math.sin(i)) * 0.6),
        draft: saved, original: initial, jobs: [], aiEditing: false, layoutHasZoompan: false,
      },
    });
  });
  await page.route(/\/editor\/fx\/resolve$/, (r) => {
    const body = r.request().postDataJSON() as { document: EditDocument; style: DocFx["style"] };
    resolves.push(body);
    const fx = body.document.fx ?? planned;
    const active = body.style.intensity !== "off";
    return r.fulfill({
      json: {
        document: {
          ...body.document,
          fx: {
            ...fx, style: body.style,
            cues: active ? [...fx.cues.filter((c) => c.origin === "user"), ...RESOLVED.filter((c) => !fx.suppressed.includes(c.id))] : [],
          },
        },
      },
    });
  });
  await page.route("**/editor/exports", (r) => {
    exports.push(r.request().postDataJSON());
    return r.fulfill({ json: { id: "job", status: "queued", progress: 0 } });
  });
  await page.route("**/editor/media/clean.mp4", (r) => serve(r, mediaPath));
  await page.route("**/editor/media/thumbnails.jpg", (r) => r.fulfill({ body: readFileSync(thumbsPath), contentType: "image/jpeg" }));
  await page.goto("/tasks/one/edit?clip=clip1");
  await expect(page.getByLabel("Word 1", { exact: true })).toBeVisible();
  return { saved: () => saved, resolves, exports };
}

const lane = (page: Page) => page.getByRole("group", { name: "Dynamic edits lane" });
const cue = (page: Page, id: string) => lane(page).locator(`[data-fx-cue="${id}"]`);

async function turnOn(page: Page, resolves: { style: DocFx["style"] }[]) {
  await page.getByRole("tab", { name: "Edits", exact: true }).click();
  await page.getByRole("radio", { name: /^Bold/ }).click();
  await expect.poll(() => resolves.at(-1)?.style.intensity).toBe("bold");
  await expect(lane(page).locator("[data-fx-cue]")).toHaveCount(RESOLVED.length);
}

/** Mean brightness (0-255) of the preview canvas. */
async function previewBrightness(page: Page) {
  const png = await page.getByLabel("Edited video preview").screenshot();
  const gray = execFileSync("ffmpeg", ["-v", "error", "-i", "pipe:0", "-frames:v", "1", "-vf", "scale=32:32", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], { input: png });
  return gray.reduce((sum, v) => sum + v, 0) / gray.length;
}

test("intensity → resolve → FX lane; delete suppresses, undo restores; the preview flashes; export carries fx", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const { saved, resolves, exports } = await setup(page);
  await turnOn(page, resolves);
  await expect.poll(() => saved().document.fx?.style.intensity).toBe("bold");
  // The freeze holds 0.5 s: the finished clip is 3.5 s while the lanes stay on the 3 s edit timeline.
  await expect(page.getByText("1 segment · 0:03.50", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/editor-effects-lane-1440.png" });

  // Delete an auto cue with the keyboard: it is suppressed so a re-plan never brings it back; undo restores it.
  await cue(page, "b1.flash").click();
  await expect(cue(page, "b1.flash")).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Delete");
  await expect(cue(page, "b1.flash")).toHaveCount(0);
  await expect.poll(() => saved().document.fx?.suppressed).toEqual(["b1.flash"]);
  expect(saved().document.fx?.cues.map((c) => c.id)).not.toContain("b1.flash");
  await page.keyboard.press("Control+z");
  await expect(cue(page, "b1.flash")).toHaveCount(1);
  await expect.poll(() => saved().document.fx?.suppressed).toEqual([]);

  // The preview draws the flash: bright at its first frames, dark before it.
  await page.locator("#studio-playhead").fill("0.5");
  await expect.poll(() => previewBrightness(page)).toBeLessThan(80);
  const dark = await previewBrightness(page);
  // The flash's first frame (the range input steps in frames).
  await page.locator("#studio-playhead").fill("1");
  await expect.poll(() => previewBrightness(page), { timeout: 10_000 }).toBeGreaterThan(dark + 100);

  // Drag the zoom later: it moves and becomes the user's own.
  const box = (await cue(page, "b1.zoom").boundingBox())!;
  const laneBox = (await lane(page).boundingBox())!;
  const pxPerSecond = laneBox.width / 3;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + pxPerSecond * 0.25, box.y + box.height / 2, { steps: 5 });
  await page.mouse.move(box.x + box.width / 2 + pxPerSecond * 0.5, box.y + box.height / 2, { steps: 5 });
  await page.mouse.up();
  await expect.poll(() => saved().document.fx?.cues.find((c) => c.id === "b1.zoom")?.origin).toBe("user");
  const zoom = saved().document.fx!.cues.find((c) => c.id === "b1.zoom")!;
  expect(zoom.start).toBeGreaterThan(0.55);
  expect(zoom.end - zoom.start).toBeCloseTo(0.7, 3);

  // Double-click opens the cue in the inspector's Edits tab.
  await page.getByRole("tab", { name: "Captions", exact: true }).click();
  await cue(page, "b1.sfx").dblclick();
  await expect(page.getByRole("tab", { name: "Edits", exact: true })).toHaveAttribute("aria-selected", "true");

  // Background export sends the document with its edits.
  await page.getByRole("button", { name: "Export", exact: true }).click();
  await page.getByRole("button", { name: "Export clip", exact: true }).click();
  await expect.poll(() => exports.length).toBe(1);
  expect(exports[0].document.fx).toMatchObject({ style: { intensity: "bold" }, mood: "hype" });
  expect(exports[0].document.fx!.cues.map((c) => c.id).sort()).toEqual(["b1.flash", "b1.freeze", "b1.sfx", "b1.zoom"]);
});

test("sounds are added where the lane is double-clicked, or at the playhead from the lane header", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const { saved, resolves } = await setup(page);
  await turnOn(page, resolves);
  const box = (await lane(page).boundingBox())!;
  // Bottom row early on is free (cues sit at 0.2 s and later; Chrome hit-tests the rounded corner, so not at 0).
  // The first click puts the playhead there, so the sound lands where the lane was double-clicked.
  const x = 0.1 * box.width;
  await page.mouse.dblclick(box.x + x, box.y + box.height - 3);
  await page.getByRole("button", { name: "Riser", exact: true }).click();
  await expect.poll(() => (saved().document.fx?.cues.find((c) => c.id === "u0") as { sound?: string } | undefined)?.sound).toBe("riser");
  const added = saved().document.fx!.cues.find((c) => c.id === "u0")!;
  expect(added).toMatchObject({ kind: "sfx", origin: "user", end: added.start });
  expect(added.start).toBeCloseTo(0.3, 1);
  // The new sound is selected; Delete removes it (a user cue is not suppressed).
  await expect(cue(page, "u0")).toHaveAttribute("aria-pressed", "true");

  await page.locator("#studio-playhead").fill("2");
  await page.getByRole("button", { name: "Add sound", exact: true }).click();
  await page.getByRole("button", { name: "Pop", exact: true }).click();
  await expect.poll(() => saved().document.fx?.cues.find((c) => c.id === "u1")).toMatchObject({ sound: "pop", start: 2 });
  await cue(page, "u1").click();
  await page.keyboard.press("Backspace");
  await expect.poll(() => saved().document.fx?.cues.some((c) => c.id === "u1")).toBe(false);
  expect(saved().document.fx?.suppressed).toEqual([]);
});

for (const [width, height] of [[1440, 900], [390, 844]]) {
  test(`studio with dynamic edits at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    const { resolves } = await setup(page);
    await turnOn(page, resolves);
    await expect(lane(page)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    if (width >= 1024) {
      // The studio still fills the viewport with the extra lane; nothing scrolls the page.
      expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight + 1)).toBe(true);
      const canvas = (await page.getByLabel("Edited video preview").boundingBox())!;
      expect(canvas.height).toBeGreaterThan(height * 0.3);
    }
    // Every cue sits inside the lane.
    const laneBox = (await lane(page).boundingBox())!;
    for (const handle of await lane(page).locator("[data-fx-cue]").all()) {
      const b = (await handle.boundingBox())!;
      expect(b.y).toBeGreaterThanOrEqual(laneBox.y - 1);
      expect(b.y + b.height).toBeLessThanOrEqual(laneBox.y + laneBox.height + 1);
    }
    await lane(page).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/editor-effects-${width}.png`, fullPage: width < 1024 });
  });
}
