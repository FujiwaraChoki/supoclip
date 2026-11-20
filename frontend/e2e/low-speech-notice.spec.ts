import { expect, test, type Page } from "@playwright/test";

// A highlights task that stopped after transcription: the video has little speech.
const stopped = {
  id: "quiet", user_id: "user", source_title: "Open world free roam, no commentary", source_type: "youtube",
  source_url: "https://www.youtube.com/watch?v=aaaaaaaaaaa", status: "error", error_code: "low_speech",
  progress: 0, progress_message: "SupoClip works best on videos where people talk, and this one has very little speech, so the clips may be weak. Your minutes were refunded. To make clips anyway, resume this task or create it again.",
  clips_count: 0, source_duration_seconds: 2280, created_at: "2026-11-20T10:00:00Z", updated_at: "2026-11-20T10:02:00Z",
};

async function mockApp(page: Page, current: () => Record<string, unknown>) {
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true } } }));
  await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route("**/api/fonts", route => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", route => route.fulfill({ json: { templates: [] } }));
  await page.route("**/api/announcements**", route => route.fulfill({ json: { announcements: [] } }));
  await page.route(/\/api\/tasks\/?(\?.*)?$/, route => route.fulfill({ json: { tasks: [current()] } }));
  await page.route("**/api/tasks/quiet", route => route.fulfill({ json: current() }));
  await page.route("**/api/tasks/quiet/clips", route => route.fulfill({ json: { clips: [] } }));
  await page.route("**/api/tasks/quiet/progress", route => route.fulfill({ status: 503 }));
}

for (const viewport of [{ name: "desktop", width: 1440, height: 900 }, { name: "mobile", width: 390, height: 844 }]) {
  test(`a video with little speech asks before making clips on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    let current: Record<string, unknown> = { ...stopped };
    let resumes = 0;
    await mockApp(page, () => current);
    await page.route("**/api/tasks/quiet/resume", async route => {
      resumes++;
      current = { ...current, status: "queued", error_code: null, progress_message: "Re-queued by user" };
      await route.fulfill({ json: { message: "Generation retry queued" } });
    });

    await page.goto("/tasks/quiet");
    await expect(page.getByRole("heading", { name: "SupoClip works best with speech" })).toBeVisible();
    await expect(page.getByText(/very little talking in this video/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    if (process.env.CAPTURE_LOW_SPEECH_SCREENSHOTS) {
      await page.screenshot({ path: `test-results/low-speech-${viewport.name}.png` });
    }

    await page.getByRole("button", { name: "Create clips anyway" }).click();
    await expect.poll(() => resumes).toBe(1);
    await expect(page.getByRole("heading", { name: "SupoClip works best with speech" })).toHaveCount(0);
  });
}
