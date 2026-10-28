import { expect, test, type Page } from "@playwright/test";

async function mockHome(page: Page, configured: boolean) {
  await page.route("**/api/auth/get-session**", (route) => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true } } }));
  await page.route("**/api/fonts", (route) => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", (route) => route.fulfill({ json: { templates: [{ id: "default", name: "Default", description: "Word captions" }] } }));
  await page.route("**/api/tasks/billing-summary", (route) => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route(/\/api\/tasks\/?(\?.*)?$/, (route) => route.fulfill({ json: { tasks: [] } }));
  await page.route("**/api/announcements**", (route) => route.fulfill({ json: { announcements: [] } }));
  await page.route("**/api/service-status/video-downloads", (route) => route.fulfill({ json: { component: { status: "operational" } } }));
  await page.route("**/api/tasks/one", route => route.fulfill({ json: { id: "one", user_id: "user", source_title: "B-roll test generation", status: "queued", source_type: "youtube", clips_count: 0 } }));
  await page.route("**/api/tasks/one/clips", route => route.fulfill({ json: { clips: [] } }));
  await page.route("**/api/preferences", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/broll/status", (route) => route.fulfill({ json: { configured, provider: configured ? "pexels" : null } }));
}

for (const width of [390, 1280]) {
  test(`B-roll can be switched on and is sent with generation at ${width}px`, async ({ page }) => {
    test.setTimeout(60_000); // first destination compiles on demand in next dev
    await page.setViewportSize({ width, height: 900 });
    await mockHome(page, true);
    let submitted: Record<string, unknown> | undefined;
    await page.route("**/api/tasks/create", async (route) => {
      submitted = route.request().postDataJSON();
      await route.fulfill({ json: { task_id: "one" } });
    });
    await page.goto("/");
    const toggle = page.getByRole("button", { name: "B-roll", exact: true });
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    await page.screenshot({ path: `test-results/broll-toggle-${width}.png` });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByPlaceholder("Paste a YouTube, Twitch, or Kick VOD/clip link").fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
    await page.getByRole("button", { name: "Generate clips", exact: true }).click();
    await expect(page).toHaveURL(/\/tasks\/one$/, { timeout: 15_000 });
    expect(submitted?.include_broll).toBe(true);
  });
}

test("B-roll is not offered when the server has no stock footage provider", async ({ page }) => {
  await mockHome(page, false);
  let submitted: Record<string, unknown> | undefined;
  await page.route("**/api/tasks/create", async (route) => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ json: { task_id: "one" } });
  });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Captions", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "B-roll", exact: true })).toHaveCount(0);
  await page.getByPlaceholder("Paste a YouTube, Twitch, or Kick VOD/clip link").fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  await page.getByRole("button", { name: "Generate clips", exact: true }).click();
  await expect(page).toHaveURL(/\/tasks\/one$/, { timeout: 15_000 });
  expect(submitted?.include_broll).toBe(false);
});
