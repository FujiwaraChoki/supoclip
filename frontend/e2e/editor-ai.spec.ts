import { expect, test, type Page, type Route } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import type { EditDocument } from "../src/lib/editor/document";
import { draft } from "../src/lib/editor/document.test-fixture";

const mediaPath = path.resolve("test-results/ai-clean.mp4");
const thumbsPath = path.resolve("test-results/ai-thumbnails.jpg");
test.beforeAll(() => {
  mkdirSync(path.dirname(mediaPath), { recursive: true });
  execFileSync("ffmpeg", [
    "-v", "error", "-y", "-f", "lavfi", "-i", "gradients=size=1080x1920:rate=24:duration=3:speed=0.02:c0=0x1c1917:c1=0xf97316:c2=0x0ea5e9:c3=0xf5f5f4:nb_colors=4",
    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", mediaPath,
  ]);
  // A filmstrip sprite like the one the editor prepares.
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

async function setup(page: Page, { aiEditing = true } = {}) {
  let saved: { revision: number; document: EditDocument } = { revision: 0, document: structuredClone(draft) };
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
    r.fulfill({ json: { id: "one", user_id: "user", source_title: "Try something new for 30 days", status: "completed", source_type: "upload", clips_count: 3 } }),
  );
  await page.route("**/api/tasks/one/clips", (r) =>
    r.fulfill({
      json: {
        clips: [1, 2, 3].map((n) => ({
          id: `clip${n}`, filename: `clip${n}.mp4`, clip_order: n, duration: 3,
          text: ["Hello world today", "Thirty days is the right amount of time", "What are you waiting for?"][n - 1],
          video_url: "/x",
        })),
      },
    }),
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
        status: "ready", basis: "clip1.mp4", duration: 3, width: 1080, height: 1920, fps: 24, hasAudio: true,
        waveform: Array.from({ length: 160 }, (_, i) => 0.2 + Math.abs(Math.sin(i)) * 0.6),
        draft: saved, original: draft, jobs: [], aiEditing,
      },
    });
  });
  await page.route("**/editor/media/clean.mp4", (r) => serve(r, mediaPath));
  await page.route("**/editor/media/thumbnails.jpg", (r) => r.fulfill({ body: readFileSync(thumbsPath), contentType: "image/jpeg" }));
  await page.goto("/tasks/one/edit?clip=clip1");
  await expect(page.getByLabel("Word 1", { exact: true })).toBeVisible();
  return { saved: () => saved };
}

/** Answers the AI endpoints: queued, then a result built from the request. */
async function mockAI(page: Page, answer: (request: { document: EditDocument; instruction: string }) => object, delayPolls = 1) {
  const requests: { document: EditDocument; instruction: string; playhead: number; basis: string }[] = [];
  let polls = 0;
  await page.route("**/editor/ai", (r) => {
    requests.push(r.request().postDataJSON());
    return r.fulfill({ status: 202, json: { id: "a".repeat(32), status: "queued" } });
  });
  await page.route(`**/editor/ai/${"a".repeat(32)}`, (r) => {
    polls += 1;
    if (polls <= delayPolls) return r.fulfill({ json: { status: "running" } });
    return r.fulfill({ json: { status: "completed", ...answer(requests[requests.length - 1]) } });
  });
  return requests;
}

for (const [width, height] of [[1440, 900], [1280, 720]]) {
  test(`full-screen studio layout at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await setup(page);
    // No page scroll: the studio fills the viewport and panels scroll inside.
    expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight + 1)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const canvas = (await page.getByLabel("Edited video preview").boundingBox())!;
    expect(canvas.height).toBeGreaterThan(height * 0.35);
    await expect(page.getByLabel("Ask AI to edit this clip")).toBeVisible();
    // The app's own navigation is gone; the editor's back button replaces it.
    await expect(page.getByRole("navigation", { name: /main/i })).toBeHidden();
    await page.screenshot({ path: `test-results/editor-layout-${width}.png` });
    // Clips are switched (and combined) from the picker in the header.
    await page.getByRole("button", { name: "Switch clip", exact: true }).click();
    await expect(page.getByRole("button", { name: "Edit clip 3", exact: true })).toBeVisible();
    await page.getByRole("checkbox", { name: /clip 2/ }).click();
    await page.getByRole("checkbox", { name: /clip 3/ }).click();
    await expect(page.getByRole("button", { name: /Combine 2 drafts/ })).toBeVisible();
    await page.screenshot({ path: `test-results/editor-clip-picker-${width}.png` });
    await page.keyboard.press("Escape");
  });
}

test("an AI edit is applied as one undoable change", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const { saved } = await setup(page);
  const requests = await mockAI(page, ({ document }) => ({
    summary: "Made the captions uppercase and cut the first second.",
    notes: [],
    changed: true,
    length: 2,
    document: {
      ...document,
      segments: [{ id: "s", start: 1, end: 3 }],
      captions: { ...document.captions, uppercase: true },
    },
  }));

  const prompt = page.getByLabel("Ask AI to edit this clip");
  await prompt.fill("make captions uppercase and cut the intro");
  await prompt.press("Enter");

  await expect(page.getByText("Made the captions uppercase and cut the first second.")).toBeVisible();
  await expect(page.getByText(/Now 0:02.00 \(was 0:03.00\)/)).toBeVisible();
  expect(requests[0]).toMatchObject({ instruction: "make captions uppercase and cut the intro", basis: "clip1.mp4" });
  expect(typeof requests[0].playhead).toBe("number");
  await expect.poll(() => saved().document.captions.uppercase).toBe(true);
  expect(saved().document.segments).toEqual([{ id: "s", start: 1, end: 3 }]);
  await page.screenshot({ path: "test-results/editor-ai-result.png" });

  await page.getByRole("button", { name: "Undo", exact: true }).last().click();
  await expect.poll(() => saved().document.captions.uppercase).toBeFalsy();
  expect(saved().document.segments).toEqual(draft.segments);
});

test("edits made while the AI works are never overwritten", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const { saved } = await setup(page);
  await mockAI(
    page,
    ({ document }) => ({ summary: "Muted the clip.", changed: true, length: 3, document: { ...document, muted: true } }),
    3,
  );
  await page.getByLabel("Ask AI to edit this clip").fill("mute it");
  await page.getByRole("button", { name: "Run", exact: true }).click();
  // The creator keeps editing while the AI works.
  await page.getByRole("tab", { name: "Framing" }).click();
  await page.getByRole("radio", { name: "Square", exact: true }).click();

  await expect(page.getByText("Muted the clip.")).toBeVisible();
  await expect(page.getByText(/edited the clip while the AI worked/)).toBeVisible();
  await expect.poll(() => saved().document.framing.aspect).toBe("square");
  expect(saved().document.muted).toBe(false);

  await page.getByRole("button", { name: "Apply", exact: true }).click();
  await expect.poll(() => saved().document.muted).toBe(true);
});

test("AI failures are shown and the draft is unchanged", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const { saved } = await setup(page);
  await page.route("**/editor/ai", (r) =>
    r.fulfill({ status: 429, json: { detail: "You've reached today's limit for AI edits. It resets tomorrow." } }),
  );
  await page.keyboard.press("Control+j");
  await expect(page.getByLabel("Ask AI to edit this clip")).toBeFocused();
  await page.keyboard.type("add music");
  await page.keyboard.press("Enter");
  await expect(page.getByText(/today's limit for AI edits/)).toBeVisible();
  expect(saved().revision).toBe(0);
});

test("the AI prompt is hidden when AI editing isn't configured", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await setup(page, { aiEditing: false });
  await expect(page.getByLabel("Ask AI to edit this clip")).toHaveCount(0);
});

test("repeated Enter during a slow job sends one request and a no-op preserves history", async ({ page }) => {
  const { saved } = await setup(page);
  const requests = await mockAI(page, () => ({
    summary: "Music cannot be added here.", changed: false, notes: [],
  }), 4);
  const prompt = page.getByLabel("Ask AI to edit this clip");
  await prompt.fill("add music");
  await prompt.press("Enter");
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await expect(prompt).toBeDisabled();
  await expect(page.getByText("Music cannot be added here.")).toBeVisible({ timeout: 10_000 });
  expect(requests).toHaveLength(1);
  expect(saved().revision).toBe(0);
  await expect(prompt).toBeEnabled();
});

test("an expired result can be retried without losing the draft", async ({ page }) => {
  const { saved } = await setup(page);
  let attempts = 0;
  await page.route("**/editor/ai", r => {
    attempts++;
    return r.fulfill({ status: 202, json: { id: "a".repeat(32) } });
  });
  await page.route(`**/editor/ai/${"a".repeat(32)}`, r => attempts === 1
    ? r.fulfill({ status: 404, json: { detail: "AI edit not found" } })
    : r.fulfill({ json: { status: "completed", summary: "Muted after retry.", changed: true, document: { ...draft, muted: true } } }));
  const prompt = page.getByLabel("Ask AI to edit this clip");
  await prompt.fill("mute it");
  await prompt.press("Enter");
  await expect(page.getByText("AI edit not found")).toBeVisible();
  expect(saved().revision).toBe(0);
  await prompt.fill("mute it");
  await prompt.press("Enter");
  await expect(page.getByText("Muted after retry.")).toBeVisible();
  await expect.poll(() => saved().document.muted).toBe(true);
  expect(attempts).toBe(2);
});

test("switching clips cancels the old job and ignores its late response", async ({ page }) => {
  const { saved } = await setup(page);
  await page.route(/\/api\/tasks\/one\/clips\/clip2\/editor$/, r => r.fulfill({ json: {
    status: "ready", basis: "clip2.mp4", duration: 3, width: 1080, height: 1920, fps: 24,
    hasAudio: true, waveform: [], draft: { revision: 0, document: draft }, original: draft, jobs: [], aiEditing: true,
  } }));
  let release!: () => void;
  let started!: () => void;
  const polling = new Promise<void>(resolve => { started = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/editor/ai", r => r.fulfill({ status: 202, json: { id: "a".repeat(32) } }));
  await page.route(`**/editor/ai/${"a".repeat(32)}`, async r => {
    started();
    await gate;
    await r.fulfill({ json: { status: "completed", summary: "Old result.", changed: true, document: { ...draft, muted: true } } });
  });
  await page.getByLabel("Ask AI to edit this clip").fill("mute it");
  await page.getByRole("button", { name: "Run", exact: true }).click();
  await polling;
  await page.getByRole("button", { name: "Switch clip", exact: true }).click();
  await page.getByRole("button", { name: "Edit clip 2", exact: true }).click();
  await expect(page).toHaveURL(/clip=clip2/);
  await expect(page.getByLabel("Ask AI to edit this clip")).toBeEnabled();
  release();
  // A cancelled request may never produce a response, so wait for the new preview instead.
  await expect(page.getByLabel("Word 1", { exact: true })).toBeVisible();
  await expect(page.getByText("Old result.")).toHaveCount(0);
  expect(saved().revision).toBe(0);
});

test("long unbroken Unicode requests and summaries stay inside a narrow editor", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  const text = "x".repeat(800) + "東京🙂".repeat(25);
  await mockAI(page, () => ({ summary: text, changed: false, notes: [text] }), 0);
  const prompt = page.getByLabel("Ask AI to edit this clip");
  await prompt.fill(text);
  await prompt.press("Enter");
  const result = page.getByRole("region", { name: "Video preview" }).getByRole("status");
  await expect(result).toContainText(text);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const status = await result.boundingBox();
  expect(status!.x + status!.width).toBeLessThanOrEqual(390);
  expect(status!.height).toBeLessThanOrEqual(128);
});
