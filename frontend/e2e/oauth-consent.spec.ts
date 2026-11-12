import { expect, test, type Page } from "@playwright/test";

const request = {
  client_name: "Claude",
  redirect_host: "claude.ai",
  scopes: ["supoclip"],
  email_verified: true,
  at_key_limit: false,
  max_keys: 25,
};

async function mockConsent(page: Page, body: object, status = 200) {
  await page.route("**/api/auth/get-session**", (route) => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true } } }));
  await page.route("**/api/oauth/requests/req-1", (route) => route.fulfill({ status, json: body }));
}

test("a connector asks for access and is sent back with a code", async ({ page }) => {
  await mockConsent(page, request);
  let decision: unknown = null;
  await page.route("**/api/oauth/requests/req-1/decision", async (route) => {
    decision = route.request().postDataJSON();
    await route.fulfill({ json: { redirect_url: "http://127.0.0.1:3118/oauth-done?code=abc&state=s" } });
  });
  await page.route("**/oauth-done**", (route) => route.fulfill({ contentType: "text/html", body: "<p>back at the connector</p>" }));

  await page.goto("/oauth/consent?request=req-1");
  await expect(page.getByRole("heading", { name: "Allow Claude to use your SupoClip account?" })).toBeVisible();
  await expect(page.getByText("Spend your plan's video minutes")).toBeVisible();
  await page.screenshot({ path: "test-results/oauth-consent.png", fullPage: true });

  await page.getByRole("button", { name: "Allow" }).click();
  await expect(page.getByText("back at the connector")).toBeVisible();
  expect(decision).toEqual({ approve: true });
});

test("at the key limit Allow is off and settings are linked", async ({ page }) => {
  await mockConsent(page, { ...request, at_key_limit: true });
  await page.goto("/oauth/consent?request=req-1");
  await expect(page.getByText(/You already have 25 active API keys/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Allow" })).toBeDisabled();
  await page.screenshot({ path: "test-results/oauth-consent-limit.png", fullPage: true });
});

test("an expired request says how to start again", async ({ page }) => {
  await mockConsent(page, { detail: { code: "request_expired", message: "expired" } }, 404);
  await page.goto("/oauth/consent?request=req-1");
  await expect(page.getByRole("heading", { name: "This request has expired" })).toBeVisible();
});
