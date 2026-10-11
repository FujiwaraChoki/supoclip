import { expect, test } from "@playwright/test";
import type { ManagedAnnouncement } from "../src/lib/announcements";

const creatorAnnouncement: ManagedAnnouncement = {
  id: "creator-program-2026-10", title: "The creator program is open", description: "Make content about SupoClip and get hosted Pro for free while participating as an approved creator. Your audience gets 20% off their first three months. No cash payments or referral commissions.",
  publishedAt: "2026-10-09T00:00:00Z", href: "/affiliate", action: "Explore the creator program", isPublished: true,
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/announcements", route => route.fulfill({ json: { announcements: [creatorAnnouncement] } }));
});

test("landing announcement opens with the keyboard and remembers that it was read", async ({ page }) => {
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: null }));
  await page.goto("/");
  const badge = page.getByRole("button", { name: "What's new, 1 unread announcement" });
  await badge.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "The creator program is open" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Explore the creator program" })).toHaveAttribute("href", "/affiliate");
  await page.screenshot({ animations: "disabled", path: "test-results/announcements-landing.png" });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "What's new", exact: true })).toBeFocused();
  await page.reload();
  await expect(page.getByRole("button", { name: "What's new", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /unread announcement/ })).toHaveCount(0);
});

test("in-app announcements work on desktop and mobile without covering the navigation", async ({ page }) => {
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: {
    session: { id: "session", userId: "announcement-user", expiresAt: "2099-01-01T00:00:00Z" },
    user: { id: "announcement-user", name: "Alex", email: "alex@example.com", emailVerified: true },
  } }));
  await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route(/\/api\/tasks\/?(\?.*)?$/, route => route.fulfill({ json: { tasks: [] } }));
  await page.goto("/list");
  await page.getByRole("button", { name: /What's new, 1 unread announcement/ }).filter({ visible: true }).click();
  await expect(page.getByRole("heading", { name: "The creator program is open" })).toBeVisible();
  await page.screenshot({ animations: "disabled", path: "test-results/announcements-app-desktop.png" });
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "What's new", exact: true }).filter({ visible: true }).click();
  await expect(page.getByRole("heading", { name: "The creator program is open" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Mobile navigation" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ animations: "disabled", path: "test-results/announcements-app-mobile.png" });
});

test("admins create a draft, publish, edit and hide it through the announcements API", async ({ page }) => {
  let items: ManagedAnnouncement[] = [];
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: {
    session: { id: "session", userId: "admin", expiresAt: "2099-01-01T00:00:00Z" },
    user: { id: "admin", name: "Alex", email: "alex@example.com", is_admin: true, emailVerified: true },
  } }));
  await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route("**/api/announcements", route => route.fulfill({ json: { announcements: items.filter(item => item.isPublished) } }));
  await page.route("**/api/admin/announcements", route => {
    if (route.request().method() === "POST") {
      const item = { ...route.request().postDataJSON(), id: "database-id" } as ManagedAnnouncement;
      items.push(item);
      return route.fulfill({ status: 201, json: { announcement: item } });
    }
    return route.fulfill({ json: { announcements: items } });
  });
  await page.route("**/api/admin/announcements/*", route => {
    const body = route.request().postDataJSON() as ManagedAnnouncement;
    items = items.map(item => ({ ...item, ...body }));
    return route.fulfill({ json: { success: true } });
  });
  await page.goto("/admin/announcements");
  await page.getByRole("button", { name: "New announcement", exact: true }).click();
  await page.getByLabel("Title", { exact: true }).fill("New caption styles");
  await page.getByLabel("Description", { exact: true }).fill("Try our new caption styles on your next clip.");
  await page.getByLabel("Destination path", { exact: true }).fill("/settings");
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByText("Draft", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /unread announcement/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(page.getByText("Published", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /1 unread announcement/ }).filter({ visible: true })).toBeVisible();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Title", { exact: true }).fill("More caption styles");
  await page.getByRole("button", { name: "Save and publish", exact: true }).click();
  await expect(page.getByRole("heading", { name: "More caption styles", exact: true })).toBeVisible();
  await page.screenshot({ animations: "disabled", path: "test-results/announcements-admin.png" });
  await page.getByRole("button", { name: "Hide", exact: true }).click();
  await expect(page.getByText("Draft", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /What's new/ })).toHaveCount(0);
  expect(items[0]).toMatchObject({ id: "database-id", title: "More caption styles", isPublished: false });
});
