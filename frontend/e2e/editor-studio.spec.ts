import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { draft } from "../src/lib/editor/document.test-fixture";

const generation = {
  id: "one",
  user_id: "user",
  source_title: "A conversation worth sharing",
  status: "completed",
  source_type: "upload",
  clips_count: 2,
};
const clips = [1, 2].map((n) => ({
  id: `clip${n}`,
  filename: `clip${n}.mp4`,
  clip_order: n,
  duration: 3,
  text: "Hello world today",
  video_url: `/tasks/one/clips/clip${n}/file`,
}));
const mediaPath = path.resolve("test-results/studio-source.mp4");
test.beforeAll(() => {
  mkdirSync(path.dirname(mediaPath), { recursive: true });
  execFileSync("ffmpeg", [
    "-v",
    "error",
    "-y",
    "-f",
    "lavfi",
    "-i",
    "testsrc2=size=640x360:rate=24:duration=3",
    "-f",
    "lavfi",
    "-i",
    "sine=frequency=440:sample_rate=48000:duration=3",
    "-c:v",
    "libx264",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "aac",
    "-movflags",
    "+faststart",
    mediaPath,
  ]);
});
async function setup(page: Page, failSave = false) {
  const documents = new Map(
    clips.map((c) => [c.id, { revision: 0, document: structuredClone(draft) }]),
  );
  await page.route("**/api/auth/get-session**", (r) =>
    r.fulfill({
      json: {
        session: {
          id: "session",
          userId: "user",
          expiresAt: "2099-01-01T00:00:00Z",
        },
        user: {
          id: "user",
          name: "Alex",
          email: "alex@example.com",
          emailVerified: true,
        },
      },
    }),
  );
  await page.route("**/api/tasks/one", (r) => r.fulfill({ json: generation }));
  await page.route("**/api/tasks/one/clips", (r) =>
    r.fulfill({ json: { clips } }),
  );
  await page.route("**/api/fonts/*", (r) =>
    r.fulfill({
      body: readFileSync(
        path.resolve(
          "../backend/fonts",
          path.basename(new URL(r.request().url()).pathname) + ".ttf",
        ),
      ),
      contentType: "font/ttf",
    }),
  );
  await page.route(/\/api\/tasks\/one\/clips\/clip[12]\/editor$/, async (r) => {
    const id = r.request().url().includes("clip1") ? "clip1" : "clip2";
    const saved = documents.get(id)!;
    if (r.request().method() === "PATCH") {
      if (failSave)
        return r.fulfill({
          status: 503,
          json: { detail: "Save temporarily unavailable" },
        });
      const body = r.request().postDataJSON();
      if (body.revision !== saved.revision)
        return r.fulfill({
          status: 409,
          json: { detail: "A newer draft exists" },
        });
      documents.set(id, {
        revision: saved.revision + 1,
        document: body.document,
      });
      return r.fulfill({ json: { revision: saved.revision + 1 } });
    }
    return r.fulfill({
      json: {
        status: "ready",
        basis: `${id}.mp4`,
        duration: 3,
        width: 640,
        height: 360,
        fps: 24,
        hasAudio: true,
        waveform: Array.from(
          { length: 160 },
          (_, i) => 0.2 + Math.abs(Math.sin(i)) * 0.6,
        ),
        draft: saved,
        original: draft,
        jobs: [],
      },
    });
  });
  await page.route("**/editor/media/clean.mp4", (r) => {
    const bytes = readFileSync(mediaPath),
      range = r
        .request()
        .headers()
        ["range"]?.match(/bytes=(\d+)-(\d*)/);
    const start = range ? Number(range[1]) : 0,
      end = range && range[2] ? Number(range[2]) : bytes.length - 1;
    return r.fulfill({
      status: range ? 206 : 200,
      body: bytes.subarray(start, end + 1),
      contentType: "video/mp4",
      headers: {
        "Accept-Ranges": "bytes",
        "Content-Length": String(end - start + 1),
        ...(range
          ? { "Content-Range": `bytes ${start}-${end}/${bytes.length}` }
          : {}),
      },
    });
  });
  await page.route("**/editor/media/thumbnails.jpg", (r) =>
    r.fulfill({ status: 404 }),
  );
  await page.goto("/tasks/one/edit?clip=clip1");
  await expect(page.getByLabel("Word 1", { exact: true })).toBeVisible();
  await expect
    .poll(() =>
      page.locator("video").evaluate((v) => (v as HTMLVideoElement).readyState),
    )
    .toBeGreaterThanOrEqual(2);
  return documents;
}

test("drafts survive clip switching and reload, undo restores an edit", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await setup(page);
  await page.getByRole("tab", { name: "Framing" }).click();
  await page.getByRole("slider", { name: "Zoom", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(
    page.getByRole("slider", { name: "Zoom", exact: true }),
  ).toHaveAttribute("aria-valuenow", "1.01");
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(
    page.getByRole("slider", { name: "Zoom", exact: true }),
  ).toHaveAttribute("aria-valuenow", "1");
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await page.getByRole("button", { name: "Switch clip", exact: true }).click();
  await page.getByRole("button", { name: "Edit clip 2", exact: true }).click();
  await page.getByRole("button", { name: "Switch clip", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Edit clip 2", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Edit clip 1", exact: true }).click();
  await page.getByRole("tab", { name: "Framing" }).click();
  await expect(
    page.getByRole("slider", { name: "Zoom", exact: true }),
  ).toHaveAttribute("aria-valuenow", "1.01");
  await page.reload();
  await page.getByRole("tab", { name: "Framing" }).click();
  await expect(
    page.getByRole("slider", { name: "Zoom", exact: true }),
  ).toHaveAttribute("aria-valuenow", "1.01");
  expect(errors).toEqual([]);
});

test("failed saves retain local edits after reload", async ({ page }) => {
  await setup(page, true);
  await page.getByLabel("Word 1", { exact: true }).fill("Changed");
  await expect(
    page.getByRole("alert").filter({ hasText: "Save temporarily unavailable" }),
  ).toContainText("Save temporarily unavailable");
  await page.reload();
  await expect(page.getByLabel("Word 1", { exact: true })).toHaveValue(
    "Changed",
  );
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(page.getByLabel("Word 1", { exact: true })).toHaveValue("Hello");
});

test("splits and restores non-destructively with keyboard and precise trim", async ({
  page,
}) => {
  await setup(page);
  await page.getByLabel("In point seconds").fill("0.25");
  await page.getByLabel("Out point seconds").fill("2.75");
  await page.getByLabel("Seek to word 2", { exact: true }).click();
  await page.getByRole("button", { name: "Split at playhead" }).click();
  await expect(page.getByText("Segment 2", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "More", exact: true }).click();
  await page.getByRole("menuitem", { name: "Restore original" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Restore original", exact: true })
    .click();
  await expect(page.getByText("Segment 2", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("In point seconds")).toHaveValue("0");
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(page.getByText("Segment 2", { exact: true })).toBeVisible();
});

test("background export sends the complete edited document", async ({
  page,
}) => {
  await setup(page);
  let request: Record<string, unknown> | undefined;
  await page.route("**/editor/exports", (r) => {
    request = r.request().postDataJSON();
    return r.fulfill({ json: { id: "job", status: "queued", progress: 0 } });
  });
  await page.getByLabel("Word 1", { exact: true }).fill("Edited");
  await page.getByRole("tab", { name: "Framing" }).click();
  await page.getByRole("radio", { name: "Square", exact: true }).click();
  await page.getByRole("button", { name: "Export", exact: true }).click();
  await expect(page.getByRole("dialog").getByText(/1080 × 1080/)).toBeVisible();
  await page.getByRole("button", { name: "Export clip", exact: true }).click();
  await expect.poll(() => request).toBeTruthy();
  expect(request).toMatchObject({
    basis: "clip1.mp4",
    document: {
      framing: { aspect: "square" },
      words: [{ text: "Edited" }, { text: "world" }, { text: "today" }],
    },
  });
});

test("playback decodes changing picture frames and pause and frame-step control the video", async ({ page }) => {
  await setup(page);
  const source = page.locator('video[src$="/media/clean.mp4"]');
  const picture = () => page.getByLabel("Edited video preview").evaluate((canvas: HTMLCanvasElement) => {
    // Above the caption area: caption redraws must not masquerade as video playback.
    const pixels = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, Math.floor(canvas.height / 2)).data;
    let hash = 0, lit = 0;
    for (let i = 0; i < pixels.length; i += 16) {
      hash = (Math.imul(hash, 31) + pixels[i] + 3 * pixels[i + 1] + 7 * pixels[i + 2]) | 0;
      if (pixels[i] + pixels[i + 1] + pixels[i + 2] > 50) lit++;
    }
    return { hash, lit };
  });
  await expect.poll(async () => (await picture()).lit).toBeGreaterThan(1000);
  const before = await picture();
  const frames = await source.evaluate((v: HTMLVideoElement) => v.getVideoPlaybackQuality().totalVideoFrames);
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect.poll(() => source.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0.3);
  await expect.poll(() => source.evaluate((v: HTMLVideoElement) => v.getVideoPlaybackQuality().totalVideoFrames)).toBeGreaterThan(frames + 4);
  await expect.poll(async () => (await picture()).hash).not.toBe(before.hash);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  const paused = await source.evaluate((v: HTMLVideoElement) => v.currentTime);
  await page.waitForTimeout(200);
  expect(await source.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(paused, 2);
  await page.getByRole("button", { name: "Next frame", exact: true }).click();
  await expect.poll(() => source.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(paused + 0.03);
  expect(await source.evaluate((v: HTMLVideoElement) => v.error?.message ?? null)).toBeNull();
});

test("preview, caption dragging and responsive layout", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await setup(page);
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Pause", exact: true }),
  ).toBeVisible();
  await page.waitForTimeout(500);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  const canvas = page.getByLabel("Edited video preview"),
    box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.78);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.6);
  await page.mouse.up();
  await expect(
    page.getByRole("slider", { name: "Subtitle vertical position" }),
  ).not.toHaveAttribute("aria-valuenow", "78");
  await page.screenshot({
    path: info.outputPath("studio-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("studio-mobile.png"),
    fullPage: true,
  });
});

test("browser export contains edited video and audio through multiple cuts", async ({
  page,
}, info) => {
  await setup(page);
  await page.getByLabel("Seek to word 2", { exact: true }).click();
  await page.getByRole("button", { name: "Split at playhead" }).click();
  await page.getByRole("tab", { name: "Framing" }).click();
  await page.getByRole("radio", { name: "Original", exact: true }).click();
  await page.getByRole("button", { name: "Export", exact: true }).click();
  await page.getByRole("button", { name: /On this device/ }).click();
  const downloadPromise = page.waitForEvent("download", { timeout: 60000 });
  await page.getByRole("button", { name: "Export clip", exact: true }).click();
  const download = await downloadPromise;
  const file = info.outputPath("browser-export.mp4");
  await download.saveAs(file);
  const probe = JSON.parse(
    execFileSync(
      "ffprobe",
      ["-v", "error", "-show_streams", "-show_format", "-of", "json", file],
      { encoding: "utf8" },
    ),
  );
  expect(
    probe.streams.find((s: { codec_type: string }) => s.codec_type === "video"),
  ).toMatchObject({ width: 640, height: 360 });
  expect(
    probe.streams.some((s: { codec_type: string }) => s.codec_type === "audio"),
  ).toBe(true);
  expect(Number(probe.format.duration)).toBeCloseTo(3, 1);
});
