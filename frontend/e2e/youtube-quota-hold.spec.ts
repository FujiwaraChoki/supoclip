import { expect, test, type Page } from "@playwright/test";

// Demo data only. A YouTube upload that hits SupoClip's daily quota comes back
// as a scheduled post with hold_reason "youtube_quota".
const connections = [{ id: "youtube-1", provider: "youtube", display_name: "Desk Notes", username: null, status: "active" }];
const providers = [{ provider: "youtube", configured: true, supported_privacy_levels: ["public", "unlisted", "private"], default_privacy_level: "public" }];

function post(overrides: Record<string, unknown>) {
  return {
    id: "post-1", task_id: "one", clip_id: "clip-1", social_account_id: "youtube-1", provider: "youtube",
    status: "queued", scheduled_for: null, title: "The one rule for a clean desk setup", caption: "", hashtags: [],
    privacy_level: "public", external_post_id: null, external_url: null, error_message: null, attempts: 0,
    hold_reason: null, held_since: null, hook_type: null, hook_title: "The one rule for a clean desk setup",
    virality_score: null, clip_duration: 12, published_at: null, last_metrics_at: null, metrics: null,
    created_at: new Date().toISOString(),
    account: { display_name: "Desk Notes", username: null, avatar_url: null, status: "active" },
    ...overrides,
  };
}

async function mockClip(page: Page) {
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Demo", email: "demo@example.com", emailVerified: true } } }));
  await page.route("**/api/broll/status", route => route.fulfill({ json: { configured: false } }));
  await page.route("**/api/attribution", route => route.fulfill({ json: {} }));
  await page.route("**/api/announcements**", route => route.fulfill({ json: { announcements: [] } }));
  await page.route("**/api/service-status/**", route => route.fulfill({ json: { component: { status: "operational" } } }));
  await page.route("**/api/fonts", route => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", route => route.fulfill({ json: { templates: [{ id: "default", name: "Default" }] } }));
  await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false, can_schedule_posts: false } }));
  await page.route("**/api/tasks/one", route => route.fulfill({ json: { id: "one", user_id: "user", source_title: "A clear desk, a clear mind", source_type: "youtube", status: "completed", clips_count: 1, created_at: "2026-10-30T10:00:00Z" } }));
  await page.route("**/api/tasks/one/clips", route => route.fulfill({ json: { clips: [{ id: "clip-1", filename: "clip.mp4", clip_order: 1, duration: 12, text: "Start with less. A clear desk makes room for your best work.", hook_title: "The one rule for a clean desk setup", video_url: "/tasks/one/clips/clip-1/file" }] } }));
  await page.route("**/api/tasks/one/clips/clip-1/file**", route => route.fulfill({ status: 404 }));
  await page.route("**/api/social/providers", route => route.fulfill({ json: { enabled: true, can_schedule: false, providers } }));
  await page.route("**/api/social/connections", route => route.fulfill({ json: { connections } }));
  await page.route("**/api/social/connections/*/publishing-options", route => route.fulfill({ json: { privacy_levels: ["public", "unlisted", "private"] } }));
  await page.route("**/api/social/captions", route => route.fulfill({ json: { caption: "Start with less. Keep only what earns its place." } }));
}

for (const width of [390, 1280]) {
  test(`a post held for YouTube's daily limit says when it will publish at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockClip(page);
    // Just after the next midnight Pacific time, like the backend's hold slot.
    const releaseAt = new Date(Date.now() + 3 * 3_600_000);
    releaseAt.setUTCMinutes(4, 0, 0);
    const held = post({
      status: "scheduled", scheduled_for: releaseAt.toISOString(), hold_reason: "youtube_quota",
      held_since: new Date().toISOString(),
      error_message: "YouTube's daily upload limit is reached. This clip will publish automatically after the limit resets at midnight Pacific time.",
    });
    let created = false;
    let polls = 0;
    await page.route("**/api/social/posts**", async route => {
      if (route.request().method() === "POST") {
        created = true;
        await route.fulfill({ json: { post: post({}) } });
        return;
      }
      if (!created) return route.fulfill({ json: { posts: [] } });
      polls += 1;
      await route.fulfill({ json: { posts: [polls < 2 ? post({}) : held] } });
    });

    await page.goto("/tasks/one");
    await page.getByRole("button", { name: "Preview The one rule for a clean desk setup" }).click();
    await page.getByRole("button", { name: "Publish", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Publish clip 1", exact: true });
    await expect(dialog.getByLabel("Caption", { exact: true })).toHaveValue(/Start with less/);
    await dialog.getByRole("button", { name: "Publish now", exact: true }).click();

    const time = await page.evaluate((iso) => new Intl.DateTimeFormat("en", {
      ...(new Date(iso).toDateString() === new Date().toDateString() ? {} : { weekday: "short" as const }),
      hour: "numeric", minute: "2-digit",
    }).format(new Date(iso)), releaseAt.toISOString());
    const message = `YouTube’s daily upload limit is reached. This clip will publish automatically at ${time}.`;
    await expect(page.getByText("Waiting for YouTube’s daily limit").first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(message).first()).toBeVisible();
    await page.screenshot({ path: `test-results/youtube-quota-hold-${width}.png` });

    await page.getByRole("button", { name: "Post actions" }).click();
    await expect(page.getByRole("menuitem", { name: /cancel/i })).toBeVisible();
    await page.keyboard.press("Escape");
  });
}
