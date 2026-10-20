import { expect, test } from "@playwright/test";

const id = "11111111-1111-4111-8111-111111111111";
const saved = { id, email: "creator@example.com", connected: true, plan_usage: true, model: null as string | null, enabled: false, welcome_seen: false };

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/get-session**", (route) => route.fulfill({ json: {
    session: { id: "session", userId: "creator", expiresAt: "2099-01-01T00:00:00Z" },
    user: { id: "creator", name: "Creator", email: "creator@example.com", emailVerified: true },
  } }));
  await page.route("**/api/tasks/billing-summary", (route) => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route("**/api/announcements", (route) => route.fulfill({ json: { announcements: [] } }));
});

test("shows the local ChatGPT option and recovers from failed sign-in", async ({ page }) => {
  await page.route("**/api/chatgpt/status", (route) => route.fulfill({ json: { accounts: [] } }));
  await page.route("**/api/chatgpt/authorize", (route) => route.fulfill({ status: 502, json: { detail: "Unable to contact ChatGPT. Please try again." } }));
  await page.goto("/settings/chatgpt");
  await expect(page.getByRole("heading", { name: "Use your ChatGPT plan" })).toBeVisible();
  await page.getByRole("button", { name: "Continue with ChatGPT" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Unable to contact ChatGPT" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue with ChatGPT" })).toBeEnabled();
});

test("selects a live model, confirms plan use once, and signs out on mobile", async ({ page }) => {
  const account = { ...saved };
  await page.route("**/api/chatgpt/status", (route) => route.fulfill({ json: { accounts: [account] } }));
  await page.route(`**/api/chatgpt/accounts/${id}/models`, (route) => route.fulfill({ json: { models: [{ id: "test-model", name: "Available model" }] } }));
  await page.route(`**/api/chatgpt/accounts/${id}/activate`, (route) => {
    account.enabled = true; account.model = route.request().postDataJSON().model;
    return route.fulfill({ json: { enabled: true } });
  });
  await page.route(`**/api/chatgpt/accounts/${id}/acknowledge`, (route) => {
    account.welcome_seen = true; return route.fulfill({ json: { acknowledged: true } });
  });
  await page.route(`**/api/chatgpt/accounts/${id}`, (route) => {
    account.connected = false; account.enabled = false;
    return route.fulfill({ json: { disconnected: true, revocation_confirmed: false } });
  });
  await page.goto("/settings/chatgpt?chatgpt=connected");
  await page.getByLabel("ChatGPT model", { exact: true }).selectOption("test-model");
  await page.getByRole("button", { name: "Use ChatGPT for clip analysis" }).click();
  await expect(page.getByRole("dialog")).toContainText("You’re using your ChatGPT plan");
  await page.getByRole("button", { name: "Got it" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText("Using ChatGPT plan for your clip analysis")).toBeVisible();
  await page.screenshot({ animations: "disabled", path: "test-results/chatgpt-desktop.png" });
  await page.reload();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ animations: "disabled", path: "test-results/chatgpt-mobile.png" });
  await page.getByRole("button", { name: "Sign out of ChatGPT" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Remote revocation was not confirmed" })).toBeVisible();
  await expect(page.getByText("Signed out", { exact: true })).toBeVisible();
});

test("keeps plan usage disabled when consent was not granted", async ({ page }) => {
  await page.route("**/api/chatgpt/status", (route) => route.fulfill({ json: { accounts: [{ ...saved, plan_usage: false }] } }));
  await page.goto("/settings/chatgpt");
  await expect(page.getByText(/ChatGPT plan usage is not enabled/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Use ChatGPT for clip analysis" })).toHaveCount(0);
});
