import { expect, test, type Page } from "@playwright/test";

const summary = {
  monetization_enabled: true,
  plan: "pro",
  subscription_status: "active",
  subscription_provider: "stripe",
  usage_count: 12,
  usage_limit: 50,
  remaining: 38,
  can_create_task: true,
  upgrade_required: false,
  reason: null,
  period_end: "2026-11-01T00:00:00Z",
  max_youtube_duration_seconds: 5400,
};
const free = { ...summary, plan: "free", subscription_status: "inactive", subscription_provider: null, usage_count: 0, usage_limit: 0, remaining: 0, can_create_task: false, upgrade_required: true, reason: "Choose a paid plan to process videos." };
const proAtLimit = { ...summary, usage_count: 50, remaining: 0, can_create_task: false, upgrade_required: true, reason: "Plan usage limit reached" };

async function mockApp(page: Page, billing: object) {
  await page.route("**/api/auth/get-session**", (route) => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true } } }));
  await page.route("**/api/tasks/billing-summary", (route) => route.fulfill({ json: billing }));
  await page.route("**/api/fonts", (route) => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", (route) => route.fulfill({ json: { templates: [{ id: "default", name: "Default", description: "Word captions" }] } }));
  await page.route("**/api/preferences", (route) => route.fulfill({ json: { fontFamily: "TikTokSans-Regular", fontSize: 24, fontColor: "#FFFFFF", notifyOnCompletion: true } }));
  await page.route(/\/api\/tasks\/?(\?.*)?$/, (route) => route.fulfill({ json: { tasks: [] } }));
}

test("free users explore first and see plans when they generate", async ({ page }) => {
  await mockApp(page, free);
  await page.goto("/");
  await expect(page.getByText("Pick a plan to start clipping").first()).toBeVisible();
  await page.screenshot({ path: "test-results/upgrade-home-free.png" });

  await page.getByRole("textbox").first().fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  await page.getByRole("button", { name: "Generate clips" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Turn long videos into ready-to-post clips")).toBeVisible();
  await expect(dialog.getByRole("radio")).toHaveCount(2);
  await page.screenshot({ path: "test-results/upgrade-dialog-free.png", animations: "disabled" });

  await dialog.getByRole("button", { name: "Maybe later" }).click();
  await expect(dialog).toHaveCount(0);
});

test("Pro users at their limit are offered Scale, not told to pay", async ({ page }) => {
  await mockApp(page, proAtLimit);
  await page.goto("/");
  await expect(page.getByText("You've clipped all 50 videos this period")).toBeVisible();
  await expect(page.getByText(/requires a paid plan/)).toHaveCount(0);
  await page.getByRole("button", { name: "Get Scale" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Current plan")).toBeVisible();
  await expect(dialog.getByRole("button", { name: /Continue with Scale/ })).toBeVisible();
  await page.screenshot({ path: "test-results/upgrade-dialog-limit.png", animations: "disabled" });
});

test("a too-long video opens the Scale prompt instead of a dead-end error", async ({ page }) => {
  await mockApp(page, summary);
  await page.route("**/api/tasks/create", (route) => route.fulfill({
    status: 422,
    json: { detail: { code: "VIDEO_TOO_LONG", message: "This video is 120.0 minutes long.", duration_seconds: 7200, max_duration_seconds: 5400 } },
  }));
  await page.goto("/");
  await page.getByRole("textbox").first().fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  await page.getByRole("button", { name: "Generate clips" }).click();
  await expect(page.getByRole("dialog").getByText("Scale can take this 2 h video")).toBeVisible();
  await page.screenshot({ path: "test-results/upgrade-dialog-too-long.png", animations: "disabled" });
});

test("settings shows Pro users the step up to Scale", async ({ page }) => {
  await mockApp(page, { ...summary, usage_count: 46, remaining: 4 });
  await page.goto("/settings#plan");
  await expect(page.getByText("Get 6× more videos with Scale")).toBeVisible();
  await page.locator("#plan").screenshot({ path: "test-results/upgrade-settings-pro.png" });
});

test("resuming past the limit opens the upgrade dialog", async ({ page }) => {
  await mockApp(page, proAtLimit);
  const task = { id: "one", user_id: "user", source_title: "A conversation worth sharing", source_type: "youtube", status: "cancelled", clips_count: 0, created_at: "2026-09-16T10:00:00Z", updated_at: "2026-09-16T10:00:00Z" };
  await page.route("**/api/tasks/one", (route) => route.fulfill({ json: task }));
  await page.route("**/api/tasks/one/clips", (route) => route.fulfill({ json: { clips: [] } }));
  await page.route("**/api/tasks/one/resume", (route) => route.fulfill({
    status: 402,
    json: { detail: { code: "SUBSCRIPTION_REQUIRED", message: "Plan usage limit reached", billing: proAtLimit } },
  }));
  await page.goto("/tasks/one");
  await page.getByRole("button", { name: "Resume" }).click();
  await expect(page.getByRole("dialog").getByText("You've clipped all 50 videos on Pro")).toBeVisible();
});
