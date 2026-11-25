import { expect, test, type Page } from "@playwright/test";

const summary = {
  monetization_enabled: true,
  plan: "pro",
  subscription_status: "active",
  subscription_provider: "stripe",
  usage_count: 4,
  usage_limit: null,
  remaining: null,
  usage_minutes: 120,
  minute_limit: 600,
  remaining_minutes: 480,
  output_short_side: 720,
  legacy_hd: false,
  billing_interval: "month",
  can_create_task: true,
  upgrade_required: false,
  reason: null,
  period_end: "2026-11-01T00:00:00Z",
  max_youtube_duration_seconds: 5400,
};
const free = { ...summary, plan: "free", subscription_status: "inactive", subscription_provider: null, usage_count: 0, usage_limit: 0, remaining: 0, usage_minutes: 0, minute_limit: 0, remaining_minutes: 0, can_create_task: false, upgrade_required: true, reason: "Choose a paid plan to process videos." };
const proAtLimit = { ...summary, usage_minutes: 600, remaining_minutes: 0, can_create_task: false, upgrade_required: true, reason: "You've used this month's video minutes." };

async function mockApp(page: Page, billing: object) {
  await page.route("**/api/auth/get-session**", (route) => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true } } }));
  await page.route("**/api/tasks/billing-summary", (route) => route.fulfill({ json: billing }));
  await page.route("**/api/fonts", (route) => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", (route) => route.fulfill({ json: { templates: [{ id: "default", name: "Default", description: "Word captions" }] } }));
  await page.route("**/api/preferences", (route) => route.fulfill({ json: { fontFamily: "TikTokSans-Regular", fontSize: 24, fontColor: "#FFFFFF", notifyOnCompletion: true } }));
  await page.route(/\/api\/tasks\/?(\?.*)?$/, (route) => route.fulfill({ json: { tasks: [] } }));
  // Home waits for these before it shows the form; stubbed so `next dev` never compiles the proxy mid-test.
  await page.route("**/api/onboarding/status**", (route) => route.fulfill({ status: 404, json: {} }));
  await page.route("**/api/agent/status", (route) => route.fulfill({ status: 401, json: {} }));
}

test.describe("creator-code checkout", () => {
  test("retains a validated house code when choosing a plan", async ({ page }) => {
    await mockApp(page, free);
    // Demo transports for the real settings UI. Account routing and the Stripe
    // guard are exercised separately by the offer route's server tests.
    await page.route("**/api/affiliates/offer?**", (route) =>
      route.fulfill({ json: { code: "DEMOHOUSE" } }));
    await page.route("**/api/billing/checkout", (route) =>
      route.fulfill({ json: { url: "https://checkout.example/demo" } }));
    await page.route("https://checkout.example/demo", (route) =>
      route.fulfill({ contentType: "text/html", body: "Demo Checkout transport" }));

    await page.goto("/settings#plan");
    await page.getByRole("button", { name: "Have a creator code?" }).click();
    await page.getByRole("textbox", { name: "Creator code" }).fill("demohouse");
    await page.getByRole("button", { name: "Apply", exact: true }).click();
    await expect(page.getByText("Creator code DEMOHOUSE: 20% off your first 3 months.")).toBeVisible();
    await page.locator("#plan").screenshot({ path: "test-results/creator-code-applied.png" });

    const checkout = page.waitForRequest((request) =>
      request.url().endsWith("/api/billing/checkout") && request.method() === "POST");
    await page.getByRole("button", { name: /Choose Pro/ }).click();
    expect((await checkout).postDataJSON()).toMatchObject({ plan: "pro", code: "DEMOHOUSE" });
    await expect(page).toHaveURL("https://checkout.example/demo");
  });
});

test("free users explore first and see plans when they generate", async ({ page }) => {
  await mockApp(page, free);
  await page.goto("/");
  await expect(
    page.getByRole("main").getByText("One long video. A week of shorts.", { exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: "test-results/upgrade-home-free.png" });

  await page.getByRole("textbox").first().fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  await page.getByRole("button", { name: "Generate clips" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Turn long videos into ready-to-post clips")).toBeVisible();
  await expect(dialog.getByRole("radiogroup", { name: "Plans" }).getByRole("radio")).toHaveCount(2);
  await expect(dialog.getByText("Self-hosted", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("link", { name: "Self-hosting guide" })).toHaveAttribute(
    "href",
    "/blog/self-host-supoclip-docker",
  );
  await dialog.getByRole("radio", { name: /Yearly/ }).click();
  await expect(dialog.getByText("$99.90")).toBeVisible();
  await expect(dialog.getByText("$8.33/mo")).toBeVisible();
  await page.waitForTimeout(2200);
  await page.screenshot({ path: "test-results/upgrade-dialog-free.png" });

  await dialog.getByRole("button", { name: "Maybe later" }).click();
  await expect(dialog).toHaveCount(0);
});

test("Pro users at their limit are offered Creator, not told to pay", async ({ page }) => {
  await mockApp(page, proAtLimit);
  await page.goto("/");
  await expect(page.getByText("You've used all 10 hours of video this month")).toBeVisible();
  await expect(page.getByText(/requires a paid plan/)).toHaveCount(0);
  await page.getByRole("button", { name: "Get Creator" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Current plan")).toBeVisible();
  await expect(dialog.getByText("Self-hosted", { exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: /Continue with Creator/ })).toBeVisible();
  await page.waitForTimeout(2200);
  await page.screenshot({ path: "test-results/upgrade-dialog-limit.png" });
});

test("a too-long video offers a part of it and the Creator plan instead of a dead-end error", async ({ page }) => {
  await mockApp(page, summary);
  await page.route("**/api/tasks/create", (route) => route.fulfill({
    status: 422,
    json: { detail: { code: "VIDEO_TOO_LONG", message: "This video is 120.0 minutes long.", duration_seconds: 7200, max_duration_seconds: 5400, range_allowed: true } },
  }));
  await page.goto("/");
  await page.getByRole("textbox").first().fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  await page.getByRole("button", { name: "Generate clips" }).click();
  const panel = page.getByTestId("source-range-panel");
  await expect(panel.getByText("This 2 h video is over your 90-minute limit")).toBeVisible();
  await page.route("**/api/billing/checkout", (route) => route.fulfill({ json: { url: "https://checkout.example/creator" } }));
  await page.route("https://checkout.example/creator", (route) => route.fulfill({ contentType: "text/html", body: "Demo Checkout transport" }));
  const checkout = page.waitForRequest((request) => request.url().endsWith("/api/billing/checkout") && request.method() === "POST");
  await panel.getByRole("button", { name: "Upgrade to Creator (up to 180 min per video)" }).click();
  expect((await checkout).postDataJSON()).toMatchObject({ plan: "scale", interval: "month" });
  await expect(page).toHaveURL("https://checkout.example/creator");
});

test("settings shows Pro users the step up to Creator and yearly billing", async ({ page }) => {
  await mockApp(page, { ...summary, usage_minutes: 540, remaining_minutes: 60 });
  await page.goto("/settings#plan");
  await expect(page.getByText("Get 2.5× more video in 1080p with Creator")).toBeVisible();
  await expect(page.getByText("Switch to yearly and get 2 months free")).toBeVisible();
  await expect(page.getByText("1 hour of video left", { exact: false })).toBeVisible();
  await expect(page.getByText("Clips in 720p", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Save Preferences" })).toHaveCount(0);
  await page.locator("#plan").screenshot({ path: "test-results/upgrade-settings-pro.png" });

  await page.getByRole("switch").first().click();
  await expect(page.getByText("Unsaved changes")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save Preferences" })).toBeVisible();
});

test("retrying past the limit opens the upgrade dialog", async ({ page }) => {
  await mockApp(page, proAtLimit);
  const task = { id: "one", user_id: "user", source_title: "A conversation worth sharing", source_type: "youtube", status: "cancelled", clips_count: 0, created_at: "2026-09-16T10:00:00Z", updated_at: "2026-09-16T10:00:00Z" };
  await page.route("**/api/tasks/one", (route) => route.fulfill({ json: task }));
  await page.route("**/api/tasks/one/clips", (route) => route.fulfill({ json: { clips: [] } }));
  await page.route("**/api/tasks/one/resume", (route) => route.fulfill({
    status: 402,
    json: { detail: { code: "SUBSCRIPTION_REQUIRED", message: "Plan usage limit reached", billing: proAtLimit } },
  }));
  await page.goto("/tasks/one");
  await page.getByRole("button", { name: "Retry generation" }).click();
  await expect(page.getByRole("dialog").getByText("You've used all 10 hours on Pro this month")).toBeVisible();
});

test("a video longer than the minutes left says how short they are", async ({ page }) => {
  const short = { ...summary, remaining_minutes: 20 };
  await mockApp(page, short);
  await page.route("**/api/tasks/create", (route) => route.fulfill({
    status: 402,
    json: { detail: { code: "SUBSCRIPTION_REQUIRED", message: "This video is 45 minutes long and you have 20 minutes left this month.", billing: { ...short, upgrade_required: true, required_minutes: 45 } } },
  }));
  await page.goto("/");
  await page.getByRole("textbox").first().fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  await page.getByRole("button", { name: "Generate clips" }).click();
  await expect(page.getByRole("dialog").getByText("This video needs 45 minutes, and you have 20 left")).toBeVisible();
});

test("locked custom fonts open a font-specific prompt", async ({ page }) => {
  await mockApp(page, free);
  await page.goto("/");
  await page.getByRole("button", { name: /^Settings:/ }).click();
  await page.getByRole("tab", { name: /Captions/ }).click();
  // Font, size and colours sit under Customize, which starts closed.
  await page.getByRole("button", { name: /^Customize/ }).click();
  await page.getByRole("button", { name: /Use your own fonts/ }).click();
  await expect(page.getByRole("dialog").getByText("Captions in your brand's own font")).toBeVisible();
  await page.waitForTimeout(2200);
  await page.screenshot({ path: "test-results/upgrade-dialog-fonts.png" });
});

test("sign-up shows the long-video-to-clips animation", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route("**/api/auth/get-session**", (route) => route.fulfill({ json: null }));
  await page.goto("/sign-up");
  await expect(page.getByText("podcast-episode-42.mp4", { exact: false })).toBeAttached();
  await page.waitForTimeout(6500);
  await page.screenshot({ path: "test-results/auth-sign-up.png" });
});
