import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { draft } from "../src/lib/editor/document.test-fixture";
import type { EditDocument } from "../src/lib/editor/document";

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
async function setup(page: Page, failSave = false, filmstrip?: string, initial: EditDocument = draft, layout = false) {
  const documents = new Map(
    clips.map((c) => [c.id, { revision: 0, document: structuredClone(initial) }]),
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
        hasLayout: layout,
        landscapeRecommended: layout,
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
  await page.route(/\/editor\/media\/(clean|layout)\.mp4$/, (r) => {
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
    filmstrip ? r.fulfill({ body: filmstrip, contentType: "image/svg+xml" }) : r.fulfill({ status: 404 }),
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

for (const width of [1440, 390]) {
  test(`filmstrip preserves round shapes after trimming and zooming at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const sprite = `<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="90">${Array.from({ length: 12 }, (_, i) =>
      `<rect x="${i * 160}" width="160" height="90" fill="black"/><circle cx="${i * 160 + 80}" cy="45" r="25" fill="#00ff00"/>`,
    ).join("")}</svg>`;
    await setup(page, false, sprite);
    const circles = async () => {
      const png = await page.getByRole("button", { name: /^Segment 1,/ }).screenshot();
      const w = png.readUInt32BE(16), h = png.readUInt32BE(20);
      const rgb = execFileSync("ffmpeg", ["-v", "error", "-i", "pipe:0", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], { input: png });
      const columns: { x: number; top: number; bottom: number }[] = [];
      for (let x = 0; x < w; x++) {
        let top = h, bottom = -1;
        for (let y = 0; y < h; y++) {
          const i = (y * w + x) * 3;
          if (rgb[i + 1] > 200 && rgb[i] < 40 && rgb[i + 2] < 40) { top = Math.min(top, y); bottom = y; }
        }
        if (bottom >= 0) columns.push({ x, top, bottom });
      }
      const groups: { left: number; right: number; top: number; bottom: number }[] = [];
      for (const c of columns) {
        const last = groups.at(-1);
        if (!last || c.x > last.right + 1) groups.push({ left: c.x, right: c.x, top: c.top, bottom: c.bottom });
        else { last.right = c.x; last.top = Math.min(last.top, c.top); last.bottom = Math.max(last.bottom, c.bottom); }
      }
      // Exclude the first thumbnail's segment label and the partially clipped final frame.
      return groups.slice(1, -1)
        .map(g => (g.right - g.left + 1) / (g.bottom - g.top + 1));
    };
    for (const trimmed of [false, true]) {
      if (trimmed) {
        await page.getByRole("spinbutton", { name: "In point seconds" }).fill("0.6");
        await page.getByRole("spinbutton", { name: "Out point seconds" }).fill("2.4");
      }
      await expect.poll(async () => (await circles()).length).toBeGreaterThan(0);
      for (const ratio of await circles()) expect(Math.abs(ratio - 1)).toBeLessThan(0.12);
      await page.getByRole("button", { name: "Zoom in", exact: true }).click();
      await expect.poll(async () => (await circles()).length).toBeGreaterThan(0);
      for (const ratio of await circles()) expect(Math.abs(ratio - 1)).toBeLessThan(0.12);
    }
  });
}

test("splitting through a spoken word does not duplicate its caption", async ({ page }) => {
  await setup(page);
  for (let i = 0; i < 6; i++) await page.getByRole("button", { name: "Next frame", exact: true }).click();
  await page.getByRole("button", { name: "Split at playhead", exact: true }).click();
  await expect(page.getByRole("button", { name: /^Segment 2,/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Hello world today", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Hello Hello world today", exact: true })).toHaveCount(0);
});

test("hook title is edited inline on its timed track with undo, redo and cancel", async ({ page }) => {
  await setup(page);
  const title = page.getByRole("textbox", { name: "Hook title", exact: true });
  await title.fill("A headline worth watching");
  const lane = page.getByRole("group", { name: "Hook title", exact: true });
  const clip = lane.getByRole("button", { name: "Hook title: A headline worth watching", exact: true });
  await expect(clip).toBeVisible();
  // A 3s edit shows the title from 0.12s until 2.75s, leaving the closing margin.
  await expect(clip).toHaveAttribute("title", /0:00.12 – 0:02.75/);
  await page.getByRole("tab", { name: "Audio", exact: true }).click();
  await clip.click();
  const editor = lane.getByRole("textbox", { name: "Hook title", exact: true });
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(editor).toBeFocused();
  await editor.fill("A revised headline");
  await editor.press("Enter");
  const revised = lane.getByRole("button", { name: "Hook title: A revised headline", exact: true });
  await expect(revised).toBeVisible();
  await revised.click();
  await editor.fill("Uncommitted text");
  await editor.press("Escape");
  await expect(revised).toBeVisible();
  await revised.click();
  await editor.fill("");
  await editor.press("Enter");
  await expect(lane).toHaveCount(0);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(revised).toBeVisible();
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await expect(lane).toHaveCount(0);
});

for (const width of [1440, 390]) test(`titles and captions edit in place as separate preview layers at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1100 });
  const documents = await setup(page);
  await page.getByRole("textbox", { name: "Hook title", exact: true }).fill("Editable title");
  const lane = page.getByRole("group", { name: "Hook title", exact: true });
  await lane.getByRole("button").click();
  await lane.getByRole("textbox").press("Escape");
  const preview = page.getByRole("region", { name: "Video preview", exact: true });
  await preview.getByRole("button", { name: "Video preview: Hook title: Editable title", exact: true }).click();
  const title = preview.getByRole("textbox", { name: "Hook title", exact: true });
  await expect(title).toBeFocused();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await title.fill("Inline title ✨");
  await title.press("Enter");
  await expect(lane.getByRole("button", { name: "Hook title: Inline title ✨", exact: true })).toBeVisible();
  await expect(preview.locator("canvas")).toHaveCount(3); // picture, title, captions
  const pictureHash = () => preview.getByLabel("Edited video preview", { exact: true }).evaluate((canvas: HTMLCanvasElement) => {
    const pixels = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height).data;
    let hash = 0;
    for (let i = 0; i < pixels.length; i += 16) hash = (Math.imul(hash, 31) + pixels[i] + pixels[i + 1] + pixels[i + 2]) | 0;
    return hash;
  });
  const pictureBefore = await pictureHash();
  await preview.getByRole("button", { name: "Video preview: Hello world today", exact: true }).click();
  const caption = preview.getByRole("textbox", { name: "Captions", exact: true });
  await caption.fill("Hello bright world today");
  await caption.press("Enter");
  await expect.poll(() => documents.get("clip1")!.document.words.map(word => word.text).join(" ")).toBe("Hello bright world today");
  await expect(page.getByRole("button", { name: "Hello bright world today", exact: true })).toBeVisible();
  expect(await pictureHash()).toBe(pictureBefore); // text edits never repaint words into the picture
  await preview.getByRole("button", { name: "Video preview: Hello bright world today", exact: true }).click();
  await caption.fill("Cancelled change");
  await caption.press("Escape");
  await expect(preview.getByRole("button", { name: "Video preview: Hello bright world today", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(page.getByRole("button", { name: "Hello world today", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  const block = page.getByRole("button", { name: "Hello bright world today", exact: true });
  await block.click();
  const timelineCaption = page.getByRole("textbox", { name: "Captions", exact: true });
  await timelineCaption.fill("A timeline caption");
  await timelineCaption.press("Tab"); // blur commits too
  await expect(page.getByRole("button", { name: "A timeline caption", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "A timeline caption", exact: true })).toBeVisible();
  await expect(lane.getByRole("button", { name: "Hook title: Inline title ✨", exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("inline-text-layers.png"), fullPage: true });
});

for (const width of [1440, 390]) test(`text layers drag and resize with one undo per gesture at ${width}px`, async ({ page }) => {
  test.setTimeout(60_000); // two layers, multiple autosaves, undo and redo, then reload
  await page.setViewportSize({ width, height: 1100 });
  const documents = await setup(page);
  await page.getByRole("textbox", { name: "Hook title", exact: true }).fill("Move this title");
  const titleTrack = page.getByRole("group", { name: "Hook title", exact: true });
  await titleTrack.getByRole("button").click();
  await titleTrack.getByRole("textbox").press("Escape");
  const preview = page.getByRole("region", { name: "Video preview", exact: true });
  await preview.getByLabel("Edited video preview").scrollIntoViewIfNeeded();
  const frame = (await preview.getByLabel("Edited video preview").boundingBox())!;
  const saved = () => documents.get("clip1")!.document;
  const transform = (doc: EditDocument, layer: "hook" | "captions") => layer === "captions" ? Object.values(doc.captionTransforms ?? {})[0] : doc.textTransforms?.hook;
  await expect.poll(() => saved().hookTitle).toBe("Move this title");
  for (const [layer, label] of [["hook", "Hook title"], ["captions", "Captions"]] as const) {
    await preview.getByLabel("Edited video preview").scrollIntoViewIfNeeded();
    const box = preview.getByRole("group", { name: `Video preview: ${label}`, exact: true });
    const original = structuredClone(saved());
    const rect = (await box.boundingBox())!;
    const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + frame.width * .03, y + frame.height * .02, { steps: 3 });
    await page.waitForTimeout(850); // a stationary pause must not split history
    await page.mouse.move(x + frame.width * .1, y + frame.height * (layer === "hook" ? .15 : -.15), { steps: 5 });
    await page.mouse.up();
    await expect.poll(() => transform(saved(), layer)?.x).toBeCloseTo(.1, 2);
    await expect.poll(() => transform(saved(), layer)?.y).toBeCloseTo(layer === "hook" ? .15 : -.15, 2);
    const moved = structuredClone(saved());
    await page.getByRole("button", { name: "Undo", exact: true }).click();
    await expect.poll(() => saved()).toEqual(original);
    await page.getByRole("button", { name: "Redo", exact: true }).click();
    await expect.poll(() => saved()).toEqual(moved);
    await preview.getByLabel("Edited video preview").scrollIntoViewIfNeeded();
    const beforeResize = (await box.boundingBox())!;
    const handle = box.getByRole("button", { name: `Resize ${label}, bottom right`, exact: true });
    const grip = (await handle.boundingBox())!;
    await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
    await page.mouse.down();
    await page.mouse.move(grip.x + grip.width / 2 + beforeResize.width * .2, grip.y + grip.height / 2 + beforeResize.height * .2, { steps: 5 });
    await page.mouse.up();
    await expect.poll(() => transform(saved(), layer)?.scale).toBeCloseTo(1.2, 1);
    const afterResize = (await box.boundingBox())!;
    expect(afterResize.x).toBeCloseTo(beforeResize.x, 0);
    expect(afterResize.y).toBeCloseTo(beforeResize.y, 0);
    expect(afterResize.width).toBeGreaterThan(beforeResize.width * 1.1);
    const resized = structuredClone(saved());
    await page.getByRole("button", { name: "Undo", exact: true }).click();
    await expect.poll(() => saved()).toEqual(moved);
    await page.getByRole("button", { name: "Redo", exact: true }).click();
    await expect.poll(() => saved()).toEqual(resized);
    const other = layer === "hook" ? "captions" : "hook";
    expect(resized.textTransforms?.[other]).toEqual(original.textTransforms?.[other]);
    // Capture must survive leaving the video, then stop on release.
    await preview.getByLabel("Edited video preview").scrollIntoViewIfNeeded();
    const text = (await box.boundingBox())!;
    await page.mouse.move(text.x + text.width / 2, text.y + text.height / 2);
    await page.mouse.down();
    await page.mouse.move(0, 0, { steps: 5 });
    await page.mouse.up();
    const canvas = (await preview.getByLabel("Edited video preview").boundingBox())!;
    await expect.poll(async () => (await box.boundingBox())!.x).toBeGreaterThanOrEqual(canvas.x);
    await expect.poll(async () => (await box.boundingBox())!.y).toBeGreaterThanOrEqual(canvas.y);
    await expect.poll(() => transform(saved(), layer)).not.toEqual(transform(resized, layer));
    const released = structuredClone(saved());
    await page.mouse.move(text.x, text.y, { steps: 3 });
    await page.getByRole("tab", { name: "Audio", exact: true }).click();
    expect(saved()).toEqual(released);
    await page.getByRole("button", { name: "Undo", exact: true }).click();
    await expect.poll(() => saved()).toEqual(resized);
  }
  const final = structuredClone(saved());
  await page.reload();
  await expect.poll(() => saved()).toEqual(final);
  await expect(page.getByRole("button", { name: "Hook title: Move this title", exact: true })).toBeVisible();
});

for (const width of [1440, 390]) test(`moving and resizing one caption leaves neighbors and repeated footage unchanged at ${width}px`, async ({ page }) => {
  test.setTimeout(60_000); // autosaves plus history restoration after reload
  await page.setViewportSize({ width, height: 1100 });
  const initial: EditDocument = { ...structuredClone(draft), captions: { ...draft.captions, wordsPerLine: 1 }, segments: [draft.segments[0], { id: "repeat", start: 0, end: 3 }] };
  const documents = await setup(page, false, undefined, initial);
  const saved = () => documents.get("clip1")!.document;
  const preview = page.getByRole("region", { name: "Video preview", exact: true });
  const box = preview.getByRole("group", { name: "Video preview: Captions", exact: true });
  const go = async (word: string, occurrence = 0) => {
    await page.getByRole("button", { name: word, exact: true }).nth(occurrence).click();
    await page.getByRole("textbox", { name: "Captions", exact: true }).press("Escape");
    await preview.getByLabel("Edited video preview").scrollIntoViewIfNeeded();
    await expect(box).toBeVisible();
  };
  const geometry = async () => {
    const frame = (await preview.getByLabel("Edited video preview").boundingBox())!, rect = (await box.boundingBox())!;
    return { x: (rect.x - frame.x) / frame.width, y: (rect.y - frame.y) / frame.height, width: rect.width / frame.width, height: rect.height / frame.height };
  };
  await go("world"); const neighbor = await geometry();
  await go("Hello", 1); const repeated = await geometry();
  await go("Hello");
  const initialGeometry = await geometry();
  const frame = (await preview.getByLabel("Edited video preview").boundingBox())!, first = (await box.boundingBox())!;
  await page.mouse.move(first.x + first.width / 2, first.y + first.height / 2);
  await page.mouse.down();
  await page.mouse.move(first.x + first.width / 2 + frame.width * .1, first.y + first.height / 2 - frame.height * .15, { steps: 5 });
  await page.mouse.up();
  await expect.poll(() => Object.values(saved().captionTransforms ?? {})[0]?.x).toBeCloseTo(.1, 2);
  const moved = structuredClone(saved());
  const size = (await box.boundingBox())!, grip = (await box.getByRole("button", { name: "Resize Captions, bottom right", exact: true }).boundingBox())!;
  await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
  await page.mouse.down();
  await page.mouse.move(grip.x + grip.width / 2 + size.width * .25, grip.y + grip.height / 2 + size.height * .25, { steps: 5 });
  await page.mouse.up();
  await expect.poll(() => Object.values(saved().captionTransforms ?? {})[0]?.scale).toBeCloseTo(1.25, 1);
  expect(saved().captions).toEqual(initial.captions);
  expect(Object.keys(saved().captionTransforms!)).toEqual(['["s","a"]']);
  const resized = structuredClone(saved()), editedGeometry = await geometry();
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect.poll(() => saved()).toEqual(moved);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect.poll(() => saved()).toEqual(initial);
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await expect.poll(() => saved()).toEqual(resized);
  await go("world"); expect(await geometry()).toEqual(neighbor);
  await go("Hello", 1); expect(await geometry()).toEqual(repeated);
  await page.reload();
  await go("Hello"); expect(await geometry()).toEqual(editedGeometry);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect.poll(() => saved().captionTransforms ?? {}).toEqual({});
  expect(await geometry()).toEqual(initialGeometry);
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await expect.poll(async () => (await geometry()).width).toBeCloseTo(editedGeometry.width, 6);
});

for (const width of [1440, 390]) test(`title duration edges drag, persist and undo at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1100 });
  const initial: EditDocument = { ...structuredClone(draft), hookTitle: "Timed title", segments: [draft.segments[0], { id: "again", start: 0, end: 3 }] };
  const documents = await setup(page, false, undefined, initial);
  const end = page.getByRole("slider", { name: "Title end, seconds", exact: true });
  const start = page.getByRole("slider", { name: "Title start, seconds", exact: true });
  const track = page.getByRole("group", { name: "Hook title", exact: true });
  await end.scrollIntoViewIfNeeded();
  const box = (await end.boundingBox())!, lane = (await track.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + lane.width / 6, box.y + box.height / 2, { steps: 8 });
  await page.mouse.move(box.x + box.width / 2 + lane.width / 6, box.y - 20); await page.mouse.up();
  await expect.poll(() => documents.get("clip1")!.document.hookTiming?.end).toBeCloseTo(5, 1);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect.poll(() => documents.get("clip1")!.document.hookTiming ?? null).toBeNull();
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await expect.poll(() => documents.get("clip1")!.document.hookTiming?.end).toBeCloseTo(5, 1);
  await start.focus(); await start.press("ArrowRight");
  await expect.poll(() => documents.get("clip1")!.document.hookTiming?.start).toBeCloseTo(.22, 2);
  await page.reload();
  await expect(end).toHaveAttribute("aria-valuenow", /^4\.9|^5/);
  await expect(start).toHaveAttribute("aria-valuenow", "0.22");
});

test("custom title timing controls its transparent preview layer", async ({ page }) => {
  await setup(page, false, undefined, { ...structuredClone(draft), hookTitle: "Timed title", hookTiming: { start: .75, end: 2.25 } });
  const pixels = () => page.getByRole("region", { name: "Video preview", exact: true }).getByLabel("Hook title", { exact: true }).evaluate((c: HTMLCanvasElement) => c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data.some((v, i) => i % 4 === 3 && v > 0));
  for (const [time, visible] of [[.5, false], [1.5, true], [2.5, false]] as const) {
    await page.getByRole("slider", { name: "Playhead", exact: true }).evaluate((e: HTMLInputElement, time) => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(e, String(time));
      e.dispatchEvent(new Event("input", { bubbles: true }));
      e.dispatchEvent(new Event("change", { bubbles: true }));
    }, time);
    await expect.poll(pixels).toBe(visible);
  }
});

for (const width of [1440, 390]) test(`timeline typing updates the video before committing at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1100 });
  const initial = { ...structuredClone(draft), hookTitle: "Original title" };
  const documents = await setup(page, false, undefined, initial);
  const preview = page.getByRole("region", { name: "Video preview", exact: true });
  const timeline = page.getByRole("region", { name: "Clip timeline", exact: true });
  const pixels = (label: string) => preview.getByLabel(label, { exact: true }).evaluate((canvas: HTMLCanvasElement) => {
    let hash = 0, lit = 0;
    const data = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height).data;
    for (let i = 0; i < data.length; i += 4) {
      hash = (Math.imul(hash, 31) + data[i] + data[i + 1] * 3 + data[i + 2] * 7 + data[i + 3]) | 0;
      if (data[i + 3]) lit++;
    }
    return { hash, lit };
  });
  for (const [label, before, after] of [["Captions", "Hello world today", "Hello corrected today"], ["Hook title", "Original title", "Corrected title"]]) {
    const buttonName = label === "Hook title" ? `Hook title: ${before}` : before;
    await timeline.getByRole("button", { name: buttonName, exact: true }).click();
    const input = timeline.getByRole("textbox", { name: label, exact: true });
    await expect(input).toBeFocused();
    await expect.poll(async () => (await pixels(label)).lit).toBeGreaterThan(10);
    const normal = await pixels(label);
    await input.press("ControlOrMeta+a");
    await input.press("Backspace");
    await expect.poll(async () => (await pixels(label)).lit).toBe(0);
    await input.pressSequentially(after);
    await expect.poll(async () => (await pixels(label)).hash).not.toBe(normal.hash);
    await expect(input).toBeFocused();
    expect(documents.get("clip1")!.document).toEqual(initial);
    await input.press("Escape");
    await expect.poll(() => pixels(label)).toEqual(normal);
    await timeline.getByRole("button", { name: buttonName, exact: true }).click();
    await input.press("ControlOrMeta+a");
    await input.pressSequentially(after);
    await expect.poll(async () => (await pixels(label)).hash).not.toBe(normal.hash);
    const typed = await pixels(label);
    await input.press("Enter");
    await expect.poll(() => pixels(label)).toEqual(typed);
    const text = () => label === "Hook title" ? documents.get("clip1")!.document.hookTitle : documents.get("clip1")!.document.words.map(w => w.text).join(" ");
    await expect.poll(text).toBe(after);
    await page.getByRole("button", { name: "Undo", exact: true }).click();
    await expect.poll(text).toBe(before);
    await expect.poll(() => pixels(label)).toEqual(normal);
    await page.getByRole("button", { name: "Redo", exact: true }).click();
    await expect.poll(text).toBe(after);
    await expect.poll(() => pixels(label)).toEqual(typed);
    Object.assign(initial, structuredClone(documents.get("clip1")!.document));
  }
  await page.reload();
  await expect(timeline.getByRole("button", { name: "Hello corrected today", exact: true })).toBeVisible();
  await expect(timeline.getByRole("button", { name: "Hook title: Corrected title", exact: true })).toBeVisible();
});

for (const dpr of [1, 2]) test.describe(`text editing parity at DPR ${dpr}`, () => {
  test.use({ deviceScaleFactor: dpr });
  test("keeps text pixels, sizing and wrapping stable when editing", async ({ page }, info) => {
    await page.setViewportSize({ width: 1440, height: 1100 });
    await setup(page);
    const headline = "A headline that stays sharp while editing";
    await page.getByRole("textbox", { name: "Hook title", exact: true }).fill(headline);
    const track = page.getByRole("group", { name: "Hook title", exact: true });
    await track.getByRole("button").click();
    await track.getByRole("textbox").press("Escape");
    await expect.poll(() => page.evaluate(() => document.fonts.check('20px "Editor-TikTokSans-Regular"'))).toBe(true);
    const preview = page.getByRole("region", { name: "Video preview", exact: true });
    const picture = preview.getByLabel("Edited video preview", { exact: true });
    await expect.poll(() => picture.evaluate((canvas: HTMLCanvasElement) => Math.abs(canvas.width - Math.min(2160, Math.round(canvas.getBoundingClientRect().width * devicePixelRatio))))).toBeLessThanOrEqual(1);
    const pixels = (index: number) => preview.locator("canvas").nth(index).evaluate((canvas: HTMLCanvasElement) => {
      const data = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height).data;
      let hash = 0, lit = 0;
      for (let i = 0; i < data.length; i += 4) {
        hash = (Math.imul(hash, 31) + data[i] + data[i + 1] * 3 + data[i + 2] * 7 + data[i + 3]) | 0;
        if (data[i + 3]) lit++;
      }
      return { hash, lit };
    });
    for (const [index, label, button, replacement] of [
      [1, "Hook title", `Video preview: Hook title: ${headline}`, "A revised sharp title"],
      [2, "Captions", "Video preview: Hello world today", "A longer replacement caption"],
    ] as const) {
      const canvas = preview.locator("canvas").nth(index);
      await expect.poll(async () => (await pixels(index)).lit).toBeGreaterThan(10);
      const before = await pixels(index);
      await preview.getByRole("button", { name: button, exact: true }).click();
      const input = preview.getByRole("textbox", { name: label, exact: true });
      await expect(input).toBeFocused();
      await expect(canvas).toBeVisible();
      await expect.poll(() => pixels(index)).toEqual(before);
      const metrics = await input.evaluate((element: HTMLTextAreaElement) => ({
        color: getComputedStyle(element).color,
        overflow: getComputedStyle(element).overflow,
        clippedX: element.scrollWidth - element.clientWidth,
        clippedY: element.scrollHeight - element.clientHeight,
      }));
      expect(metrics.color).toBe("rgba(0, 0, 0, 0)");
      expect(metrics.overflow).toBe("hidden");
      expect(metrics.clippedX).toBeLessThanOrEqual(1);
      expect(metrics.clippedY).toBeLessThanOrEqual(1);
      await page.screenshot({ path: info.outputPath(`${label}-editing.png`), fullPage: true });
      await input.fill(replacement);
      await expect.poll(async () => (await pixels(index)).hash).not.toBe(before.hash);
      await input.press("Escape");
      await expect.poll(() => pixels(index)).toEqual(before);
      await preview.getByRole("button", { name: button, exact: true }).click();
      await input.fill(replacement);
      await expect.poll(async () => (await pixels(index)).hash).not.toBe(before.hash);
      const typed = await pixels(index);
      await input.press("Enter");
      await expect(page.getByRole("button", { name: index === 1 ? `Hook title: ${replacement}` : replacement, exact: true })).toBeVisible();
      await expect.poll(() => pixels(index)).toEqual(typed);
    }
    await page.setViewportSize({ width: 960, height: 900 });
    await expect.poll(() => picture.evaluate((canvas: HTMLCanvasElement) => Math.abs(canvas.width - Math.min(2160, Math.round(canvas.getBoundingClientRect().width * devicePixelRatio))))).toBeLessThanOrEqual(1);
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


test("wide content offers landscape, preserves playhead, and supports undo and redo", async ({ page }) => {
  const initial = structuredClone(draft);
  initial.framing.fit = "auto";
  const documents = await setup(page, false, undefined, initial, true);
  await page.getByLabel("Seek to word 2", { exact: true }).click();
  const source = page.locator("video").first();
  await expect.poll(() => source.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(1, 1);
  await page.getByRole("tab", { name: "Framing" }).click();
  await expect(page.getByRole("radio", { name: "Generated layout", exact: true })).toHaveAttribute("data-state", "on");
  await page.getByRole("button", { name: "Use landscape version", exact: true }).click();
  await expect(source).toHaveAttribute("src", /clean\.mp4$/);
  await expect.poll(() => source.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(1, 1);
  await expect.poll(() => documents.get("clip1")!.document.framing.aspect).toBe("original");
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(source).toHaveAttribute("src", /layout\.mp4$/);
  await expect(page.getByRole("button", { name: "Use landscape version", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await expect(source).toHaveAttribute("src", /clean\.mp4$/);
  await expect.poll(() => documents.get("clip1")!.document.framing).toEqual({ ...initial.framing, aspect: "original", fit: "contain" });
});
