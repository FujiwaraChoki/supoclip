import { expect, test, type Page } from "@playwright/test";

const capture = Boolean(process.env.CAPTURE_THEME_SCREENSHOTS);
async function shot(page: Page, name: string) {
  if (!capture) return;
  await page.waitForTimeout(800); // let entrance animations settle
  await page.screenshot({ path: `../docs/screenshots/theme-toggle-${name}.png` });
}

const common = { user_id: "user", source_type: "youtube", status: "completed", clips_count: 3, updated_at: "2026-10-16T10:00:00Z" };
const tasks = [
  { ...common, id: "podcast", source_id: "podcast-source", source_title: "Podcast", source_url: "https://youtu.be/dQw4w9WgXcQ", created_at: "2026-10-16T10:00:00Z" },
  { ...common, id: "standup", source_id: "standup-source", source_title: "Weekly standup", source_url: "https://youtu.be/abcdefghijk", created_at: "2026-10-15T10:00:00Z" },
];

async function signedIn(page: Page) {
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: {
    session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" },
    user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true },
  } }));
  await page.route(/\/api\/tasks\/?(\?.*)?$/, route => route.fulfill({ json: { tasks } }));
  await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route("**/api/announcements**", route => route.fulfill({ json: { announcements: [] } }));
  await page.route("**/api/fonts", route => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", route => route.fulfill({ json: { templates: [{ id: "default", name: "Default", description: "Word captions" }] } }));
  await page.route("**/api/preferences", route => route.fulfill({ json: { fontFamily: "TikTokSans-Regular", fontSize: 24, fontColor: "#FFFFFF", notifyOnCompletion: true } }));
  await page.route("**/api/service-status/video-downloads", route => route.fulfill({ json: { component: { status: "operational" } } }));
  await page.route("**/i.ytimg.com/**", route => route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#374151"/></svg>' }));
}

const html = (page: Page) => page.locator("html");

test.use({ colorScheme: "dark", viewport: { width: 1440, height: 900 } });

test("marketing and auth pages stay light for a dark OS preference and a saved dark choice", async ({ page }) => {
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: null }));
  await page.goto("/");
  await expect(html(page)).toHaveClass(/\blight\b/);
  await shot(page, "landing-os-dark");

  await page.evaluate(() => localStorage.setItem("theme", "dark"));
  await page.goto("/sign-in");
  await expect(html(page)).toHaveClass(/\blight\b/);
  await expect(html(page)).not.toHaveClass(/\bdark\b/);
  await shot(page, "sign-in-saved-dark");
});

test("the app starts light, switches to dark from the sidebar, and keeps it", async ({ page }) => {
  await signedIn(page);
  await page.goto("/list");
  await expect(page.getByRole("link", { name: /Podcast/ }).first()).toBeVisible();
  await expect(html(page)).toHaveClass(/\blight\b/);
  await shot(page, "library-light");

  await page.getByRole("button", { name: "Switch light/dark theme" }).click();
  await expect(html(page)).toHaveClass(/\bdark\b/);
  await page.mouse.move(700, 450);
  await shot(page, "library-dark");

  await page.getByRole("checkbox", { name: "Select Podcast", exact: true }).check();
  await expect(page.getByRole("button", { name: /Delete/ }).last()).toBeVisible();
  await shot(page, "library-dark-bulk-bar");

  await page.goto("/");
  await expect(page.getByRole("button", { name: "Switch light/dark theme" })).toBeVisible();
  await expect(html(page)).toHaveClass(/\bdark\b/);
  await shot(page, "create-dark");

  await page.goto("/sign-in");
  await expect(html(page)).toHaveClass(/\blight\b/);
});
