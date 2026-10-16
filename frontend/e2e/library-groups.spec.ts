import { expect, test } from "@playwright/test";

const common = {
  user_id: "user", source_type: "youtube", status: "completed", clips_count: 3,
  updated_at: "2026-10-16T10:00:00Z",
};
const tasks = [
  { ...common, id: "new-cut", source_id: "new-source", source_title: "Podcast (recut)", source_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ", created_at: "2026-10-16T10:00:00Z" },
  { ...common, id: "standup", source_id: "standup-source", source_title: "Weekly standup", source_url: "https://youtu.be/abcdefghijk", created_at: "2026-10-15T10:00:00Z" },
  { ...common, id: "old-cut", source_id: "old-source", source_title: "Podcast", source_url: "https://youtu.be/dQw4w9WgXcQ", created_at: "2026-10-14T10:00:00Z" },
];

for (const viewport of [{ name: "desktop", width: 1440, height: 900 }, { name: "mobile", width: 390, height: 844 }]) {
  test(`library groups preserve selection and filtering on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.route("**/api/auth/get-session**", route => route.fulfill({ json: {
      session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" },
      user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true },
    } }));
    await page.route(/\/api\/tasks\/?(\?.*)?$/, route => route.fulfill({ json: { tasks } }));
    await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
    await page.route("**/api/announcements**", route => route.fulfill({ json: { announcements: [] } }));
    await page.route("**/i.ytimg.com/**", route => route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#374151"/></svg>' }));
    await page.goto("/list");
    await expect(page.getByRole("link", { name: /Podcast \(recut\)/ })).toBeVisible();
    await page.getByRole("checkbox", { name: "Select Podcast (recut)", exact: true }).check();
    await page.getByRole("tab", { name: "By video", exact: true }).click();
    await expect(page.getByRole("heading", { level: 2 })).toHaveText(["Podcast", "Weekly standup"]);
    await expect(page.getByRole("checkbox", { name: "Deselect Podcast (recut)", exact: true })).toBeChecked();
    expect(await page.locator('a[href^="/tasks/"]').evaluateAll(links => links.map(link => link.getAttribute("href")))).toEqual(["/tasks/new-cut", "/tasks/old-cut", "/tasks/standup"]);
    if (process.env.CAPTURE_LIBRARY_SCREENSHOTS) {
      await page.screenshot({ path: `../docs/screenshots/library-video-groups-${viewport.name}.png`, fullPage: true });
    }
    await page.getByPlaceholder("Search by title").fill("standup");
    await expect(page.getByRole("heading", { level: 2 })).toHaveText(["Weekly standup"]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
