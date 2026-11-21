import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/get-session**", (route) => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true } } }));
  await page.route("**/api/fonts", (route) => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", (route) => route.fulfill({ json: { templates: [{ id: "default", name: "Default", description: "Word captions" }] } }));
  await page.route("**/api/tasks/workflows", (route) => route.fulfill({ json: { enabled: false, workflows: [], topic_max_chars: 120, instructions_max_chars: 500 } }));
  await page.route("**/api/tasks/edit-styles", (route) => route.fulfill({ json: { enabled: false } }));
  await page.route("**/api/feature-flags", (route) => route.fulfill({ json: { platform: "web", flags: {} } }));
  await page.route("**/api/feature-tours**", (route) => route.fulfill({ status: 404, json: {} }));
  await page.route("**/api/social/providers", (route) => route.fulfill({ json: { enabled: false, can_schedule: false, providers: [] } }));
  await page.route("**/api/broll/status", (route) => route.fulfill({ json: { configured: false } }));
  await page.route("**/api/tasks/billing-summary", (route) => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route("**/api/preferences", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/announcements**", (route) => route.fulfill({ json: { announcements: [] } }));
  await page.route("**/api/service-status/video-downloads", (route) => route.fulfill({ json: { component: { status: "operational" } } }));
});

test("queues several videos and creates one task for each", async ({ page }) => {
  test.setTimeout(120_000);
  const created: Record<string, unknown>[] = [];
  await page.route(/\/api\/tasks\/?(\?.*)?$/, (route) => route.fulfill({ json: { tasks: [] } }));
  await page.route("**/api/upload/authorization", (route) => route.fulfill({
    json: { directUpload: true, uploadUrl: "/api/test-upload", headers: {} },
  }));
  await page.route("**/api/test-upload", (route) => route.fulfill({ json: { video_path: "uploads/demo.mp4" } }));
  await page.route("**/api/tasks/create", async (route) => {
    created.push(route.request().postDataJSON());
    await route.fulfill({ json: { task_id: `task-${created.length}` } });
  });

  await page.goto("/");
  await page.locator("#video-upload").setInputFiles([
    { name: "first.mp4", mimeType: "video/mp4", buffer: Buffer.from("first") },
    { name: "second.mp4", mimeType: "video/mp4", buffer: Buffer.from("second") },
  ]);

  await expect(page.getByText("first.mp4")).toBeVisible();
  await expect(page.getByText("second.mp4")).toBeVisible();
  await page.screenshot({ path: "test-results/batch-upload-queued.png" });

  await page.getByRole("button", { name: "Generate 2 videos" }).click();

  await expect(page).toHaveURL(/\/list$/, { timeout: 60_000 });
  expect(created.map((body) => (body.source as { title: string }).title)).toEqual(["first.mp4", "second.mp4"]);
  expect(created.map((body) => (body.source as { url: string }).url)).toEqual(["uploads/demo.mp4", "uploads/demo.mp4"]);
});
