import { expect, test, type Page } from "@playwright/test";

const pro = {
  monetization_enabled: true, plan: "pro", subscription_status: "active", subscription_provider: "stripe",
  usage_count: 4, usage_limit: null, remaining: null, usage_minutes: 120, minute_limit: 600, remaining_minutes: 480,
  output_short_side: 720, legacy_hd: false, billing_interval: "month", can_create_task: true, upgrade_required: false,
  reason: null, period_end: "2026-12-01T00:00:00Z", max_youtube_duration_seconds: 7200, max_upload_duration_seconds: 7200,
};

async function mockApp(page: Page, billing: Partial<typeof pro> = {}) {
  await page.route("**/api/auth/get-session**", (route) => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true } } }));
  await page.route("**/api/tasks/billing-summary", (route) => route.fulfill({ json: { ...pro, ...billing } }));
  await page.route("**/api/fonts", (route) => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", (route) => route.fulfill({ json: { templates: [{ id: "default", name: "Default", description: "Word captions" }] } }));
  await page.route("**/api/preferences", (route) => route.fulfill({ json: { fontFamily: "TikTokSans-Regular", fontSize: 24, fontColor: "#FFFFFF", notifyOnCompletion: true } }));
  await page.route(/\/api\/tasks\/?(\?.*)?$/, (route) => route.fulfill({ json: { tasks: [] } }));
  // Home waits for these before it shows the form; stubbed so `next dev` never compiles the proxy mid-test.
  await page.route("**/api/onboarding/status**", (route) => route.fulfill({ status: 404, json: {} }));
  await page.route("**/api/agent/status", (route) => route.fulfill({ status: 401, json: {} }));
}

test("an 8-hour stream: pick two hours of it and clip just that part", async ({ page }) => {
  await mockApp(page);
  const bodies: Array<Record<string, unknown>> = [];
  await page.route("**/api/tasks/create", async (route) => {
    const body = route.request().postDataJSON();
    bodies.push(body);
    if (!body.source.start_seconds) {
      await route.fulfill({ status: 422, json: { detail: {
        code: "VIDEO_TOO_LONG", message: "This Twitch video is 480.0 minutes long.",
        duration_seconds: 28800, max_duration_seconds: 7200, min_range_seconds: 60, range_allowed: true,
      } } });
      return;
    }
    await route.fulfill({ json: { task_id: "demo-task" } });
  });
  await page.route("**/tasks/demo-task**", (route) => route.fulfill({ contentType: "text/html", body: "Demo task page" }));

  await page.goto("/");
  await page.getByRole("textbox").first().fill("https://www.twitch.tv/videos/1234567890");
  await page.getByRole("button", { name: "Generate clips" }).click();

  const panel = page.getByTestId("source-range-panel");
  await expect(panel.getByText("This 8 h video is over your 120-minute limit")).toBeVisible();
  await expect(panel.getByRole("textbox", { name: "End" })).toHaveValue("2:00:00");

  // Type an exact start; the part moves with it and keeps its two hours.
  await panel.getByRole("textbox", { name: "Start" }).fill("1:30:00");
  await panel.getByRole("textbox", { name: "Start" }).press("Enter");
  await expect(panel.getByRole("textbox", { name: "End" })).toHaveValue("3:30:00");
  await expect(panel.getByText("Clipping 2:00:00. Uses 120 minutes.")).toBeVisible();

  // The keyboard moves a thumb a minute at a time.
  await panel.getByRole("slider", { name: "End" }).focus();
  await page.keyboard.press("ArrowLeft");
  await expect(panel.getByRole("textbox", { name: "End" })).toHaveValue("3:29:00");

  await panel.getByRole("button", { name: "Clip this part" }).click();
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[1].source).toMatchObject({ url: "https://www.twitch.tv/videos/1234567890", start_seconds: 5400, end_seconds: 12540 });
  expect(bodies[0].source).not.toHaveProperty("start_seconds");
});

test("a part longer than the plan allows can't be sent", async ({ page }) => {
  await mockApp(page);
  await page.route("**/api/tasks/create", (route) => route.fulfill({ status: 422, json: { detail: {
    code: "VIDEO_TOO_LONG", message: "Too long", duration_seconds: 28800, max_duration_seconds: 7200, range_allowed: true,
  } } }));
  await page.goto("/");
  await page.getByRole("textbox").first().fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  await page.getByRole("button", { name: "Generate clips" }).click();
  const panel = page.getByTestId("source-range-panel");
  await panel.getByRole("textbox", { name: "End" }).fill("4:00:00");
  await panel.getByRole("textbox", { name: "End" }).press("Enter");
  await expect(panel.getByRole("alert")).toHaveText("Pick 120 minutes or less.");
  await expect(panel.getByRole("button", { name: "Clip this part" })).toBeDisabled();
});

test("the task page says which part was clipped and links to where it starts", async ({ page }) => {
  await mockApp(page);
  const task = {
    id: "part", user_id: "user", source_title: "A long demo stream", source_type: "youtube",
    source_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ", status: "completed", clips_count: 0,
    created_at: "2026-11-21T10:00:00Z", updated_at: "2026-11-21T10:00:00Z",
    source_duration_seconds: 7200, source_start_seconds: 5400, source_end_seconds: 12600,
  };
  await page.route("**/api/tasks/part", (route) => route.fulfill({ json: task }));
  await page.route("**/api/tasks/part/clips", (route) => route.fulfill({ json: { clips: [] } }));
  await page.goto("/tasks/part");
  await expect(page.getByTestId("source-range")).toHaveText("Part 1:30:00–3:30:00");
  await expect(page.getByRole("link", { name: /YouTube/ }).first()).toHaveAttribute("href", /[?&]t=5400s/);
});

test("Creator over 180 minutes gets the picker without an upgrade", async ({ page }) => {
  await mockApp(page, { plan: "scale", max_youtube_duration_seconds: 10800, max_upload_duration_seconds: 10800, minute_limit: 1500, remaining_minutes: 1380 });
  await page.route("**/api/tasks/create", (route) => route.fulfill({ status: 422, json: { detail: {
    code: "VIDEO_TOO_LONG", message: "Too long", duration_seconds: 5 * 3600, max_duration_seconds: 10800, range_allowed: true,
  } } }));
  await page.goto("/");
  await page.getByRole("textbox").first().fill("https://kick.com/demo/videos/00000000-0000-0000-0000-000000000000");
  await page.getByRole("button", { name: "Generate clips" }).click();
  const panel = page.getByTestId("source-range-panel");
  await expect(panel.getByText("This 5 h video is over your 180-minute limit")).toBeVisible();
  await expect(panel.getByRole("button", { name: /Upgrade/ })).toHaveCount(0);
  await expect(panel.getByRole("textbox", { name: "End" })).toHaveValue("3:00:00");
});

test("a livestream that is still going gets a clear message, not a picker", async ({ page }) => {
  await mockApp(page);
  await page.route("**/api/tasks/create", (route) => route.fulfill({ status: 422, json: { detail: {
    code: "VIDEO_DURATION_UNAVAILABLE", message: "We couldn't verify this YouTube video's length.",
  } } }));
  await page.goto("/");
  await page.getByRole("textbox").first().fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  await page.getByRole("button", { name: "Generate clips" }).click();
  await expect(page.getByTestId("length-error")).toContainText("We couldn't read this video's length");
  await expect(page.getByTestId("length-error")).toContainText("wait until it ends");
  await expect(page.getByTestId("source-range-panel")).toHaveCount(0);
});
