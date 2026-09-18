import { expect, test } from "@playwright/test";

const generation = { id: "one", user_id: "user", source_title: "A conversation worth sharing", source_type: "youtube", status: "completed", clips_count: 1, created_at: "2026-09-16T10:00:00Z", updated_at: "2026-09-16T10:00:00Z" };
const clip = { id: "clip", filename: "clip.mp4", clip_order: 1, duration: 12, start_time: "00:10", end_time: "00:22", text: "A clear idea deserves a clear explanation.", video_url: "/tasks/one/clips/clip/file", relevance_score: 0.8, virality_score: 75 };

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/get-session**", (route) => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true } } }));
  await page.route("**/api/fonts", (route) => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", (route) => route.fulfill({ json: { templates: [{ id: "default", name: "Default", description: "Word captions" }] } }));
  await page.route("**/api/tasks/one", (route) => route.fulfill({ json: generation }));
  await page.route("**/api/tasks/one/clips", (route) => route.fulfill({ json: { clips: [clip] } }));
  await page.route("**/api/tasks/one/clips/clip/file**", (route) => route.fulfill({ status: 404 }));
});

test("editor retains a failed edit and previews only rendered captions", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/tasks/one/clips/clip/captions", (route) => route.fulfill({ status: 503, json: { detail: "Renderer temporarily unavailable" } }));
  await page.goto("/tasks/one/edit?clip=clip");
  await expect(page.getByRole("heading", { name: generation.source_title })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
  await page.getByRole("textbox", { name: "Subtitle text" }).fill("Keep this draft after a failed save.");
  await page.getByRole("button", { name: "Save subtitle changes" }).click();
  await expect(page.getByRole("textbox", { name: "Subtitle text" })).toHaveValue("Keep this draft after a failed save.");
  await expect(page.getByText("Renderer temporarily unavailable").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Export Selected" })).toBeDisabled();
  await expect(page.getByText("Subtitle preview", { exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
  await page.screenshot({ path: "test-results/editor-polish.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("results send editing to the selected clip and expose one status badge", async ({ page }) => {
  await page.goto("/tasks/one");
  await expect(page.getByRole("heading", { name: generation.source_title })).toBeVisible();
  await expect(page.getByText("Completed", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Edit", exact: true })).toHaveAttribute("href", "/tasks/one/edit?clip=clip");
  await expect(page.getByPlaceholder("Start trim (sec)")).toHaveCount(0);
  await page.screenshot({ path: "test-results/results-polish.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("generation list refreshes active jobs without losing selection", async ({ page }) => {
  let reads = 0;
  await page.route(/\/api\/tasks\/?$/, (route) => route.fulfill({ json: { tasks: [{ ...generation, status: ++reads === 1 ? "processing" : "completed" }] } }));
  await page.goto("/list");
  await expect(page.getByText("Processing", { exact: true })).toBeVisible();
  await page.getByRole("checkbox").last().check();
  await expect(page.getByText("Completed", { exact: true })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("checkbox").last()).toBeChecked();
});

test("saved caption edits refresh the video before export becomes available", async ({ page }) => {
  let saved = false;
  await page.route("**/api/tasks/one/clips", (route) => route.fulfill({ json: { clips: [{ ...clip,
    filename: saved ? "edited.mp4" : clip.filename,
    text: saved ? "Updated words" : clip.text,
    caption_settings: saved ? { font_size: 28, position: "middle", position_y: 0.52, highlight_words: [] } : null,
  }] } }));
  await page.route("**/api/tasks/one/clips/clip/captions", (route) => {
    expect(route.request().postDataJSON().caption_text).toBe("Updated words");
    saved = true;
    return route.fulfill({ json: { clip } });
  });
  await page.goto("/tasks/one/edit?clip=clip");
  await page.getByRole("textbox", { name: "Subtitle text" }).fill("Updated words");
  await page.getByRole("button", { name: "Save subtitle changes" }).click();
  await expect(page.getByRole("button", { name: "Export Selected" })).toBeEnabled();
  await expect(page.locator("video")).toHaveAttribute("src", /v=edited.mp4$/);
  await expect(page.getByRole("slider", { name: "Subtitle size" })).toHaveAttribute("aria-valuenow", "28");
});

test("resetting preview adjustments preserves unsaved caption changes", async ({ page }) => {
  await page.goto("/tasks/one/edit?clip=clip");
  await page.getByRole("textbox", { name: "Subtitle text" }).fill("Keep this unsaved caption.");
  const size = page.getByRole("slider", { name: "Subtitle size" });
  await size.focus();
  await size.press("ArrowRight");
  const draftSize = await size.getAttribute("aria-valuenow");
  await page.getByRole("button", { name: "Reset Preview Adjustments" }).click();
  await expect(page.getByRole("textbox", { name: "Subtitle text" })).toHaveValue("Keep this unsaved caption.");
  await expect(size).toHaveAttribute("aria-valuenow", draftSize!);
  await expect(page.getByRole("button", { name: "Save subtitle changes" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Export Selected" })).toBeDisabled();
});


test("subtitle style cannot change while save is pending", async ({ page }) => {
  let finish!: () => void;
  let started!: () => void;
  const pending = new Promise<void>(r => finish = r);
  const saving = new Promise<void>(r => started = r);
  await page.route("**/api/tasks/one/clips/clip/captions", async route => { started(); await pending; await route.fulfill({ json: { clip } }); });
  await page.goto("/tasks/one/edit?clip=clip");
  await page.getByLabel("Subtitle text").fill("Pending draft");
  const size = page.getByRole("slider", { name: "Subtitle size", exact: true });
  const before = await size.getAttribute("aria-valuenow");
  await page.getByRole("button", { name: "Save subtitle changes" }).click();
  await saving;
  await size.dispatchEvent("keydown", { key: "ArrowRight", bubbles: true });
  const after = await size.getAttribute("aria-valuenow");
  finish();
  expect(after).toBe(before);
});

test("task completion retries a transient clip-read failure", async ({ page }) => {
  let taskReads = 0;
  let clipReads = 0;
  await page.route("**/api/tasks/one", route => route.fulfill({ json: { ...generation, status: ++taskReads === 1 ? "processing" : "completed" } }));
  await page.route("**/api/tasks/one/clips", route => {
    clipReads++;
    if (clipReads === 2) return route.fulfill({ status: 503, json: { detail: "Temporary clip lookup failure" } });
    return route.fulfill({ json: { clips: clipReads === 1 ? [] : [clip] } });
  });
  await page.route("**/api/tasks/one/progress", route => route.fulfill({ status: 503 }));
  await page.goto("/tasks/one");
  await expect(page.getByText("Completed", { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("link", { name: "Edit", exact: true })).toHaveAttribute("href", "/tasks/one/edit?clip=clip");
  expect(taskReads).toBeGreaterThanOrEqual(3);
  expect(clipReads).toBeGreaterThanOrEqual(3);
  await expect(page.getByRole("heading", { name: "Something went wrong" })).toHaveCount(0);
});


test("signed-out settings displays the sign-in gate without loading preferences", async ({ page }) => {
  let preferenceReads = 0;
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: null }));
  await page.route("**/api/preferences", route => {
    preferenceReads++;
    return route.fulfill({ status: 401, json: { error: "Unauthorized" } });
  });
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Sign In Required" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign In", exact: true })).toHaveAttribute("href", "/sign-in");
  await expect(page.getByRole("button", { name: /save preferences/i })).toHaveCount(0);
  expect(preferenceReads).toBe(0);
});

test("signed-in settings waits for preferences before showing the form", async ({ page }) => {
  let finish!: () => void;
  let started!: () => void;
  const pending = new Promise<void>(resolve => finish = resolve);
  const loading = new Promise<void>(resolve => started = resolve);
  await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route("**/api/preferences", async route => {
    started();
    await pending;
    await route.fulfill({ json: { fontFamily: "TikTokSans-Regular", fontSize: 24, fontColor: "#FFFFFF", notifyOnCompletion: true } });
  });
  await page.goto("/settings");
  await loading;
  await expect(page.getByRole("button", { name: /save preferences/i })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Sign In Required" })).toHaveCount(0);
  finish();
  await expect(page.getByRole("button", { name: /save preferences/i })).toBeVisible();
});
