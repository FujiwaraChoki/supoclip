import { expect, test, type Page, type APIResponse } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const seed = JSON.parse(fs.readFileSync(path.join(process.cwd(), "e2e/.seed.json"), "utf8"));
const sourceUrl = process.env.LOCAL_TEST_YOUTUBE_URL ?? "https://www.youtube.com/watch?v=jNQXAC9IVRw";
const sourceFile = process.env.LOCAL_TEST_VIDEO_PATH;
let taskId: string;
let clipId: string;

async function ok(response: APIResponse) {
  expect(response.ok(), `${response.status()}: ${await response.text()}`).toBeTruthy();
  return response.json();
}
async function signIn(page: Page) {
  await page.goto("/sign-in");
  await page.getByPlaceholder("Email").fill(seed.regular.email);
  await page.getByPlaceholder("Password").fill(seed.regular.password);
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  await page.waitForURL("**/");
}
async function waitForTask(page: Page, id: string) {
  await expect.poll(async () => {
    const task = await ok(await page.request.get(`/api/tasks/${id}`));
    if (task.status === "error") throw new Error(task.progress_message ?? "Processing failed");
    return task.status;
  }, { timeout: 480_000, intervals: [2000, 3000, 5000] }).toBe("completed");
  return ok(await page.request.get(`/api/tasks/${id}`));
}
function probe(file: string) {
  return JSON.parse(execFileSync("ffprobe", ["-v", "error", "-show_streams", "-show_format", "-of", "json", file], { encoding: "utf8" }));
}

test.describe.serial("real local processing and editing", () => {
  test.beforeEach(async ({ page }) => { await signIn(page); });

  test("YouTube submission, live completion, playback and mobile layout", async ({ page }, info) => {
    test.setTimeout(540_000);
    await page.locator("#youtube-url").fill(sourceUrl);
    await page.getByRole("button", { name: "Process Video", exact: true }).click();
    await page.waitForURL(/\/tasks\/[^/]+$/);
    taskId = new URL(page.url()).pathname.split("/").pop()!;
    const task = await waitForTask(page, taskId);
    expect(task.clips.length).toBeGreaterThan(0);
    clipId = task.clips[0].id;
    // Completion and clips must appear without a reload.
    await expect(page.getByText("Completed", { exact: true })).toBeVisible();
    const video = page.locator("video").first();
    await expect.poll(() => video.evaluate(v => (v as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(2);
    await video.evaluate(async v => { const el = v as HTMLVideoElement; el.muted = true; await el.play(); });
    await expect.poll(() => video.evaluate(v => (v as HTMLVideoElement).currentTime)).toBeGreaterThan(0.2);
    await video.evaluate(v => (v as HTMLVideoElement).pause());
    await page.screenshot({ path: info.outputPath("results-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.screenshot({ path: info.outputPath("results-mobile.png"), fullPage: true });
  });

  test("caption rendering persists and browser export contains audio and video", async ({ page }, info) => {
    await page.goto(`/tasks/${taskId}/edit?clip=${clipId}`);
    await expect(page.getByLabel("Word 1", { exact: true })).toBeVisible({ timeout: 120_000 });
    await page.getByRole("button", { name: "Edit script", exact: true }).click();
    await page.getByLabel("Subtitle text").fill("These elephants have really long trunks. That is pretty cool.");
    await page.getByRole("button", { name: "Apply script", exact: true }).click();
    await page.getByRole("slider", { name: "Subtitle size", exact: true }).press("Home");
    await page.getByRole("slider", { name: "Subtitle size", exact: true }).press("ArrowRight");
    await expect(page.getByRole("status").filter({ hasText: "All changes saved" })).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("Word 1", { exact: true })).toHaveValue("These");
    await expect(page.getByRole("slider", { name: "Subtitle size", exact: true })).toHaveAttribute("aria-valuenow", "21");
    const video = page.locator("video");
    await expect.poll(() => video.evaluate(v => (v as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(2);
    await page.screenshot({ path: info.outputPath("editor.png"), fullPage: true });
    await page.getByRole("tab", { name: "Audio", exact: true }).click();
    const volume = page.getByRole("slider", { name: "Volume", exact: true });
    await volume.press("Home");
    for (let step = 0; step < 50; step++) await volume.press("ArrowRight");
    await page.getByRole("button", { name: "Export", exact: true }).click();
    await page.getByRole("button", { name: /On this device/ }).click();
    const download = page.waitForEvent("download", { timeout: 120_000 });
    await page.getByRole("button", { name: "Export clip", exact: true }).click();
    const file = info.outputPath("browser-export.mp4");
    await (await download).saveAs(file);
    const media = probe(file);
    expect(media.streams.some((s: { codec_type: string }) => s.codec_type === "audio")).toBeTruthy();
    expect(media.streams.find((s: { codec_type: string }) => s.codec_type === "video")).toMatchObject({ width: 1080, height: 1920 });
    expect(Number(media.format.duration)).toBeGreaterThan(10);
    const current = await ok(await page.request.get(`/api/tasks/${taskId}`));
    const original = await page.request.get(`/api/tasks/${taskId}/clips/${current.clips[0].id}/editor/media/clean.mp4`);
    const inputFile = info.outputPath("before-export.mp4");
    fs.writeFileSync(inputFile, await original.body());
    const rms = (filePath: string) => {
      const bytes = execFileSync("ffmpeg", ["-v", "error", "-i", filePath, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"], { maxBuffer: 10_000_000 });
      let sum = 0;
      for (let offset = 0; offset < bytes.length; offset += 4) sum += bytes.readFloatLE(offset) ** 2;
      return Math.sqrt(sum / (bytes.length / 4));
    };
    expect(rms(file) / rms(inputFile)).toBeGreaterThan(0.43);
    expect(rms(file) / rms(inputFile)).toBeLessThan(0.57);
  });

  test("reversible timeline edits and legacy clip API compatibility", async ({ page }) => {
    await page.goto(`/tasks/${taskId}/edit?clip=${clipId}`);
    await expect(page.getByLabel("In point seconds")).toBeVisible({ timeout: 120_000 });
    const before = await ok(await page.request.get(`/api/tasks/${taskId}`));
    await page.getByLabel("In point seconds").fill("0.25");
    await page.getByLabel("Seek to word 2", { exact: true }).click();
    await page.getByRole("button", { name: "Split at playhead" }).click();
    await expect(page.getByText("Segment 2", { exact: true })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "All changes saved" })).toBeVisible();
    const draft = await ok(await page.request.get(`/api/tasks/${taskId}/clips/${clipId}/editor`));
    expect(draft.draft.document.segments).toHaveLength(2);
    const unchanged = await ok(await page.request.get(`/api/tasks/${taskId}`));
    expect(unchanged.clips[0].filename).toBe(before.clips[0].filename);
    // Existing API/MCP clients can still commit trims, splits, and merges.
    await ok(await page.request.patch(`/api/tasks/${taskId}/clips/${clipId}`, { data: { start_offset: 0.1, end_offset: 0.1 } }));
    const trimmed = await ok(await page.request.get(`/api/tasks/${taskId}`));
    await ok(await page.request.post(`/api/tasks/${taskId}/clips/${clipId}/split`, { data: { split_time: 3 } }));
    const splitOnce = await ok(await page.request.get(`/api/tasks/${taskId}`));
    const followingClip = splitOnce.clips[1];
    await ok(await page.request.post(`/api/tasks/${taskId}/clips/${clipId}/split`, { data: { split_time: 1 } }));
    const splitTwice = await ok(await page.request.get(`/api/tasks/${taskId}`));
    expect(splitTwice.clips[2]).toMatchObject({ id: followingClip.id, filename: followingClip.filename });
    await ok(await page.request.post(`/api/tasks/${taskId}/clips/merge`, { data: { clip_ids: splitTwice.clips.map((c: { id: string }) => c.id) } }));
    const merged = await ok(await page.request.get(`/api/tasks/${taskId}`));
    expect(merged.clips).toHaveLength(1);
    clipId = merged.clips[0].id;
    expect(merged.clips[0].duration).toBeCloseTo(trimmed.clips[0].duration, 0);
    const range = await page.request.get(`/api/tasks/${taskId}/clips/${clipId}/file`, { headers: { Range: "bytes=0-1023" } });
    expect(range.status()).toBe(206);
    expect((await range.body()).length).toBe(1024);
  });

  test("sharing, revocation, ownership and server export", async ({ page, browser }, info) => {
    const share = await ok(await page.request.post(`/api/tasks/${taskId}/share`));
    const anonymous = await browser.newContext();
    const publicPage = await anonymous.newPage();
    await publicPage.goto(`/share/${share.share_token}`);
    await expect(publicPage.locator("video").first()).toBeVisible();
    expect((await publicPage.request.get(`/api/tasks/${taskId}`)).status()).toBe(401);
    const publicTask = await ok(await publicPage.request.get(`/api/share/${share.share_token}`));
    expect(publicTask.user_id).toBeUndefined();
    expect(publicTask.clips[0].file_path).toBeUndefined();
    await ok(await page.request.delete(`/api/tasks/${taskId}/share`));
    expect((await publicPage.request.get(`/api/share/${share.share_token}`)).status()).toBe(404);
    await anonymous.close();
    const result = await page.request.get(`/api/tasks/${taskId}/clips/${clipId}/export?preset=reels`, { timeout: 120_000 });
    expect(result.ok()).toBeTruthy();
    const file = info.outputPath("server-export.mp4");
    fs.writeFileSync(file, await result.body());
    expect(probe(file).streams.find((s: { codec_type: string }) => s.codec_type === "video")).toMatchObject({ width: 1080, height: 1920 });
  });

  test("upload uses direct authorization and processes a real local video", async ({ page }) => {
    test.skip(!sourceFile, "Set LOCAL_TEST_VIDEO_PATH to a real video fixture");
    test.setTimeout(540_000);
    await page.getByRole("button", { name: "Upload Video", exact: true }).click();
    await page.locator("#video-upload").setInputFiles(sourceFile!);
    await page.getByRole("button", { name: "Process Video", exact: true }).click();
    await page.waitForURL(/\/tasks\/[^/]+$/);
    const uploadedTaskId = new URL(page.url()).pathname.split("/").pop()!;
    const task = await waitForTask(page, uploadedTaskId);
    expect(task.source_type).toBe("video_url");
    expect(task.clips.length).toBeGreaterThan(0);
    await ok(await page.request.delete(`/api/tasks/${uploadedTaskId}`));
    expect((await page.request.get(`/api/tasks/${uploadedTaskId}`)).status()).toBe(404);
  });
});


test.describe("accounts and settings with real services", () => {
  test("signup, signout, incorrect password and private route protection", async ({ page }) => {
    await page.goto("/sign-up");
    await page.getByPlaceholder("Full Name").fill("Local test account");
    const email = `local-${Date.now()}@supoclip.test`;
    await page.getByPlaceholder("Email").fill(email);
    await page.getByPlaceholder("Password").fill("LocalTestPassword123!");
    await page.getByRole("button", { name: "Sign Up", exact: true }).click();
    await page.waitForURL("**/");
    expect((await page.request.get("/api/preferences")).status()).toBe(200);
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await page.waitForURL("**/sign-in");
    expect((await page.request.get("/api/tasks")).status()).toBe(401);
    await page.goto("/sign-in");
    await page.getByPlaceholder("Email").fill(email);
    await page.getByPlaceholder("Password").fill("IncorrectPassword");
    await page.getByRole("button", { name: "Sign In", exact: true }).click();
    await expect(page.getByText(/invalid email or password/i)).toBeVisible();
  });

  test("preferences persist, invalid inputs fail, API keys authenticate and revoke", async ({ page }) => {
    await signIn(page);
    const saved = await ok(await page.request.patch("/api/preferences", { data: { fontSize: 30, fontColor: "#ABCDEF", notifyOnCompletion: false } }));
    expect(saved.fontSize).toBe(30);
    expect(await ok(await page.request.get("/api/preferences"))).toMatchObject({ fontSize: 30, fontColor: "#ABCDEF", notifyOnCompletion: false });
    for (const data of [{ fontSize: 0 }, { fontSize: "30" }, { fontColor: "red" }, { notifyOnCompletion: "yes" }]) {
      expect((await page.request.patch("/api/preferences", { data })).status()).toBe(400);
    }
    const { api_key: key } = await ok(await page.request.post("/api/api-keys", { data: { name: "Local regression test" } }));
    const backend = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8100";
    expect((await page.request.get(`${backend}/tasks/`, { headers: { Authorization: `Bearer ${key.key}` } })).status()).toBe(200);
    const listing = await ok(await page.request.get("/api/api-keys"));
    expect(listing.api_keys.some((k: { key?: string }) => k.key)).toBeFalsy();
    await ok(await page.request.delete(`/api/api-keys/${key.id}`));
    expect((await page.request.get(`${backend}/tasks/`, { headers: { Authorization: `Bearer ${key.key}` } })).status()).toBe(401);
    expect((await page.request.get("/api/admin/runtime-settings")).status()).toBe(403);
  });

  test("custom font upload, retrieval and deletion", async ({ page }) => {
    await signIn(page);
    const fonts = await ok(await page.request.get("/api/fonts"));
    expect(fonts.fonts.length).toBeGreaterThan(0);
    const fontName = fonts.fonts.find((f: { scope: string }) => f.scope === "system").name;
    const fontData = await page.request.get(`/api/fonts/${fontName}`);
    expect(fontData.ok()).toBeTruthy();
    const uploaded = await ok(await page.request.post("/api/fonts/upload", { multipart: {
      file: { name: "LocalRegression.ttf", mimeType: "font/ttf", buffer: await fontData.body() },
    } }));
    const custom = uploaded.font.name;
    expect((await page.request.get(`/api/fonts/${custom}`)).status()).toBe(200);
    await ok(await page.request.delete(`/api/fonts/${custom}`));
    expect((await page.request.get(`/api/fonts/${custom}`)).status()).toBe(404);
    expect((await page.request.delete(`/api/fonts/${fontName}`)).status()).toBeGreaterThanOrEqual(400);
    const invalid = await page.request.post("/api/fonts/upload", { multipart: { file: { name: "bad.txt", mimeType: "text/plain", buffer: Buffer.from("bad") } } });
    expect(invalid.status()).toBe(400);
  });
});
