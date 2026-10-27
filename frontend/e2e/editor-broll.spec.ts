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

async function setup(page: Page) {
  let saved: { revision: number; document: EditDocument } = {
    revision: 0,
    document: { ...structuredClone(draft), framing: { ...draft.framing, aspect: "original" }, broll: [generated] },
  };
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
        waveform: Array.from({ length: 160 }, () => 0.4), draft: saved, original: saved.document, jobs: [],
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

test("browser export includes the B-roll under the captions", async ({ page }, info) => {
  await setup(page);
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
  expect(pixel(1.5).blue).toBeGreaterThan(150);
  expect(pixel(2.6).red).toBeGreaterThan(150);
});
