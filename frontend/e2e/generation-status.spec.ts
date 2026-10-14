import { expect, test } from "@playwright/test";

const incident = {
  status: "outage",
  headline: "Investigating YouTube import failures",
  description: "We are investigating failures when importing YouTube videos. The download provider API is timing out before processing starts, so new imports may fail. We are testing an alternative download path. Failed attempts that produce no clips do not use a generation allowance. Please avoid repeated retries while we work on recovery.",
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: {
    session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" },
    user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true },
  } }));
  await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route(/\/api\/tasks\/?(\?.*)?$/, route => route.fulfill({ json: { tasks: [] } }));
  await page.route("**/api/fonts", route => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", route => route.fulfill({ json: { templates: [] } }));
  await page.route("**/api/announcements**", route => route.fulfill({ json: { announcements: [] } }));
  await page.route("**/api/service-status/video-downloads", route => route.fulfill({ json: { component: incident } }));
});

for (const viewport of [{ name: "desktop", width: 1440, height: 900 }, { name: "mobile", width: 390, height: 844 }]) {
  test(`hides resolved video import status on the ${viewport.name} generation page`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.route("**/api/service-status/video-downloads", route => route.fulfill({ json: {
      component: { status: "operational", headline: "Resolved", description: "Downloads are working normally." },
    } }));
    await page.goto("/");
    await expect(page.getByRole("heading", { name: /Good .*Alex/ })).toBeVisible();
    await expect(page.getByRole("status", { name: "YouTube import status" })).toHaveCount(0);
    await expect(page.getByText("Checking YouTube import status…")).toHaveCount(0);
  });

  test(`shows the video import incident on the ${viewport.name} generation page`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/");

    const status = page.getByRole("status", { name: "YouTube import status" });
    await expect(status).toContainText("YouTube imports · Outage");
    await expect(status).toContainText(incident.headline);
    await expect(status).toContainText(incident.description);
    await expect(status.getByRole("link", { name: "View status page" })).toHaveAttribute("href", "/status");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

    if (process.env.CAPTURE_STATUS_SCREENSHOTS) {
      await page.screenshot({ path: `../docs/screenshots/generation-status-${viewport.name}.png` });
    }
  });
}
