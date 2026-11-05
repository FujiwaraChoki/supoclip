import { expect, test, type Page } from "@playwright/test";

const generation = { id: "retry", user_id: "user", source_title: "Recovery test", source_type: "youtube", status: "error", clips_count: 1, created_at: "2026-11-03T10:00:00Z", updated_at: "2026-11-03T10:00:00Z" };
const clip = { id: "saved", filename: "saved.mp4", video_url: "/tasks/retry/clips/saved/file", hook_title: "My edited clip", clip_order: 1, duration: 12, start_time: "00:10", end_time: "00:22", text: "Keep this clip", relevance_score: 0.8, virality_score: 75 };

async function mockApp(page: Page, current: () => typeof generation) {
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true } } }));
  await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route("**/api/fonts", route => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", route => route.fulfill({ json: { templates: [] } }));
  await page.route("**/api/announcements**", route => route.fulfill({ json: { announcements: [] } }));
  await page.route(/\/api\/tasks\/?(\?.*)?$/, route => route.fulfill({ json: { tasks: [current()] } }));
  await page.route("**/api/tasks/retry", route => route.fulfill({ json: current() }));
  await page.route("**/api/tasks/retry/clips", route => route.fulfill({ json: { clips: [clip] } }));
  await page.route("**/api/tasks/retry/clips/saved/**", route => route.fulfill({ status: 404 }));
  await page.route("**/api/tasks/retry/progress", route => route.fulfill({ status: 503 }));
}

test("retry keeps finished clips visible, blocks duplicate clicks, and survives reload", async ({ page }) => {
  let current = { ...generation };
  let requests = 0;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await mockApp(page, () => current);
  await page.route("**/api/tasks/retry/resume", async route => {
    requests++;
    await pending;
    current = { ...current, status: "queued" };
    await route.fulfill({ json: { message: "Generation retry queued" } });
  });
  await page.goto("/tasks/retry");
  const saved = page.getByRole("button", { name: "Preview My edited clip" });
  await expect(saved).toBeVisible();
  await expect(page.getByText(/Your completed clips will be kept/)).toBeVisible();
  const retry = page.getByRole("button", { name: "Retry generation", exact: true });
  await retry.click();
  await expect(retry).toBeDisabled();
  await expect.poll(() => requests).toBe(1);
  release();
  await expect(retry).toHaveCount(0);
  await expect(saved).toBeVisible();
  await page.reload();
  await expect(saved).toBeVisible();
  await expect(retry).toHaveCount(0);
  expect(requests).toBe(1);
});

test("a worker still stopping leaves the saved clip and retry action available", async ({ page }) => {
  await mockApp(page, () => ({ ...generation, status: "cancelled" }));
  await page.route("**/api/tasks/retry/resume", route => route.fulfill({ status: 409, json: { detail: "The previous worker is still stopping. Try again in a moment." } }));
  await page.goto("/tasks/retry");
  const retry = page.getByRole("button", { name: "Retry generation", exact: true });
  await retry.click();
  await expect(page.getByText(/The previous worker is still stopping/)).toBeVisible();
  await expect(retry).toBeEnabled();
  await expect(page.getByRole("button", { name: "Preview My edited clip" })).toBeVisible();
});

test("library bulk retry uses the new wording", async ({ page }) => {
  await mockApp(page, () => generation);
  let requests = 0;
  await page.route("**/api/tasks/retry/resume", route => {
    requests++;
    return route.fulfill({ json: { message: "Generation retry queued" } });
  });
  await page.goto("/list");
  await page.getByRole("checkbox").last().check();
  await page.getByRole("button", { name: /Retry generation/ }).click();
  await expect.poll(() => requests).toBe(1);
});

for (const viewport of [{ name: "desktop", width: 1440, height: 900 }, { name: "mobile", width: 390, height: 844 }]) {
  test(`download recovery updates survive refresh and clear on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    let current = {
      ...generation, status: "processing", progress: 10,
      progress_message: "Downloading video...",
      created_at: new Date(Date.now() - 60_000).toISOString(),
    };
    await mockApp(page, () => current);
    await page.goto("/tasks/retry");
    await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "10");

    current = { ...current, progress_message: "Using a backup video download option..." };
    const notice = page.getByRole("status", { name: "Hang tight" });
    await expect(notice).toBeVisible({ timeout: 12_000 });
    await expect(page.getByText("Hang tight")).toBeVisible();
    await expect(notice).not.toContainText(/download|backup|connection|recovery/i);
    await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "10");
    await expect(page.getByText(/min left|minute left/)).toHaveCount(0);

    await page.reload();
    await expect(notice).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    if (process.env.CAPTURE_RECOVERY_SCREENSHOTS) {
      await page.screenshot({ path: `../docs/screenshots/download-recovery-${viewport.name}.png`, fullPage: true });
    }

    current = { ...current, progress: 30, progress_message: "Generating transcript..." };
    await expect(page.getByText("Hang tight")).toHaveCount(0, { timeout: 12_000 });
    await expect(notice).toHaveCount(0);
    await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "30");
  });
}
