import { expect, test, type Page } from "@playwright/test";

const connections = [
  { id: "youtube-1", provider: "youtube", display_name: "DevBySami", username: null, status: "active" },
  { id: "tiktok-1", provider: "tiktok", display_name: "DevBySami", username: null, status: "active" },
];
const providers = [
  { provider: "youtube", configured: true, supported_privacy_levels: ["public", "unlisted", "private"], default_privacy_level: "public" },
  { provider: "tiktok", configured: true, supported_privacy_levels: ["public", "private"], default_privacy_level: "private" },
];

async function mockClip(page: Page) {
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Sami", email: "sami@example.com", emailVerified: true } } }));
  await page.route("**/api/broll/status", route => route.fulfill({ json: { configured: false } }));
  await page.route("**/api/attribution", route => route.fulfill({ json: {} }));
  await page.route("**/api/announcements**", route => route.fulfill({ json: { announcements: [] } }));
  await page.route("**/api/service-status/**", route => route.fulfill({ json: { component: { status: "operational" } } }));
  await page.route("**/api/fonts", route => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", route => route.fulfill({ json: { templates: [{ id: "default", name: "Default" }] } }));
  await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false, can_schedule_posts: true } }));
  await page.route("**/api/tasks/one", route => route.fulfill({ json: { id: "one", user_id: "user", source_title: "A clear desk, a clear mind", source_type: "youtube", status: "completed", clips_count: 1, created_at: "2026-10-30T10:00:00Z" } }));
  await page.route("**/api/tasks/one/clips", route => route.fulfill({ json: { clips: [{ id: "clip-1", filename: "clip.mp4", clip_order: 1, duration: 12, text: "Start with less. A clear desk makes room for your best work.", hook_title: "The one rule for a clean desk setup", video_url: "/tasks/one/clips/clip-1/file" }] } }));
  await page.route("**/api/tasks/one/clips/clip-1/file**", route => route.fulfill({ status: 404 }));
  await page.route("**/api/social/providers", route => route.fulfill({ json: { enabled: true, can_schedule: true, providers } }));
  await page.route("**/api/social/connections", route => route.fulfill({ json: { connections } }));
  await page.route("**/api/social/connections/*/publishing-options", route => route.fulfill({ json: { privacy_levels: ["public", "private"] } }));
  await page.route("**/api/social/captions", route => route.fulfill({ json: { caption: "Want a clutter-free desk? Start with less. The creator’s rule is simple: keep only what earns its place." } }));
}

for (const width of [390, 1280]) {
  test(`publishes to YouTube and TikTok from account selectors at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await mockClip(page);
    const submitted: Record<string, unknown>[] = [];
    await page.route("**/api/social/posts**", async route => {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON();
        submitted.push(body);
        await route.fulfill({ json: { post: { id: `post-${body.social_account_id}`, status: "queued" } } });
      } else {
        await route.fulfill({ json: { posts: [] } });
      }
    });
    await page.goto("/tasks/one");
    await page.getByRole("button", { name: "Preview The one rule for a clean desk setup" }).click();
    await page.getByRole("button", { name: "Publish", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Publish clip 1", exact: true });
    await expect(dialog.getByLabel("Caption", { exact: true })).toHaveValue(/Want a clutter-free desk/);
    await dialog.getByRole("checkbox", { name: "TikTok · DevBySami" }).check();
    await expect(dialog.getByRole("checkbox", { name: "YouTube Shorts · DevBySami" })).toBeChecked();
    await expect(dialog.getByRole("checkbox", { name: "TikTok · DevBySami" })).toBeChecked();
    const youtubeVisibility = dialog.getByLabel("Visibility · YouTube Shorts · DevBySami");
    const tiktokVisibility = dialog.getByLabel("Visibility · TikTok · DevBySami");
    await expect(youtubeVisibility).toHaveText("Public");
    await expect(tiktokVisibility).toHaveText("Choose visibility");
    await tiktokVisibility.click();
    await expect(page.getByRole("option", { name: "Unlisted", exact: true })).toHaveCount(0);
    await page.getByRole("option", { name: "Public", exact: true }).click();
    await youtubeVisibility.click();
    await page.getByRole("option", { name: "Unlisted", exact: true }).click();
    await dialog.screenshot({ path: `test-results/multi-account-publishing-${width}.png` });
    expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await dialog.getByRole("button", { name: "Publish to 2 accounts" }).click();
    await expect(dialog).not.toBeVisible();
    expect(submitted).toHaveLength(2);
    expect(submitted).toEqual(expect.arrayContaining([
      expect.objectContaining({ social_account_id: "youtube-1", privacy_level: "unlisted" }),
      expect.objectContaining({ social_account_id: "tiktok-1", privacy_level: "public" }),
    ]));
  });
}

for (const width of [390, 1280]) {
  test(`private TikTok account cannot select public at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await mockClip(page);
    await page.route("**/api/social/connections/*/publishing-options", route => route.fulfill({ json: { privacy_levels: ["private"] } }));
    await page.route("**/api/social/posts**", route => route.fulfill({ json: { posts: [] } }));
    await page.goto("/tasks/one");
    await page.getByRole("button", { name: "Preview The one rule for a clean desk setup" }).click();
    await page.getByRole("button", { name: "Publish", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Publish clip 1", exact: true });
    await dialog.getByRole("checkbox", { name: "TikTok · DevBySami" }).check();
    await dialog.getByRole("checkbox", { name: "YouTube Shorts · DevBySami" }).uncheck();
    const visibility = dialog.getByLabel("Visibility", { exact: true });
    await expect(visibility).toBeEnabled();
    await expect(visibility).toHaveText("Choose visibility");
    await expect(dialog.getByRole("button", { name: "Publish now", exact: true })).toBeDisabled();
    await expect(dialog.getByText(/Public isn’t available/)).toBeVisible();
    await visibility.click();
    await expect(page.getByRole("option", { name: "Public", exact: true })).toHaveCount(0);
    await page.getByRole("option", { name: "Private (only me)", exact: true }).click();
    await expect(dialog.getByRole("button", { name: "Publish now", exact: true })).toBeEnabled();
    await dialog.screenshot({ path: `test-results/tiktok-private-options-${width}.png` });
  });
}
