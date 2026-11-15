import { expect, test, type Page } from "@playwright/test";

// Fake account and keys only: nothing here reaches a real backend. Runs with the polish
// config (mocked, like polish.spec.ts); also writes the screenshots to SCREENSHOT_DIR.
const SHOTS = process.env.SCREENSHOT_DIR || "test-results";

async function mockAgentsPage(page: Page) {
  await page.route("**/api/auth/get-session**", (route) => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true } } }));
  await page.route("**/api/api-keys", (route) => route.fulfill({ json: { api_keys: [
    { id: "key-1", name: "Claude connector", key_prefix: "sk_demo", created_at: "2026-11-15T09:00:00Z", last_used_at: "2026-11-15T09:05:00Z", revoked: false },
  ] } }));
}

for (const viewport of [{ name: "desktop", width: 1280, height: 900 }, { name: "mobile", width: 390, height: 844 }]) {
  test(`connect Claude, ChatGPT or Grok (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockAgentsPage(page);
    await page.goto("/settings/api-keys");

    const section = page.getByRole("region", { name: "Connect Claude, ChatGPT or Grok" });
    await expect(section).toBeVisible();
    await expect(section.getByText("https://mcp.supoclip.com/mcp", { exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "API keys" }).getByText("Claude connector", { exact: true })).toBeVisible();

    const open = section.getByRole("link", { name: /Open Claude/ });
    await expect(open).toHaveAttribute("href", "https://claude.ai/customize/connectors");
    await expect(open).toHaveAttribute("rel", "noopener noreferrer");
    await section.scrollIntoViewIfNeeded();
    await section.screenshot({ path: `${SHOTS}/agents-claude-${viewport.name}.png` });
    if (viewport.name === "desktop") await page.screenshot({ path: `${SHOTS}/agents-page-${viewport.name}.png`, fullPage: true });

    await section.getByRole("tab", { name: "ChatGPT" }).click();
    await expect(section.getByRole("link", { name: /Open ChatGPT/ })).toHaveAttribute("href", "https://chatgpt.com/plugins");
    await expect(section.getByText(/Pro accounts only get read-only tools/)).toBeVisible();
    await section.screenshot({ path: `${SHOTS}/agents-chatgpt-${viewport.name}.png` });

    await section.getByRole("tab", { name: "Grok" }).click();
    await expect(section.getByRole("link", { name: /Open Grok/ })).toHaveAttribute("href", "https://grok.com/connectors");
    await expect(section.getByText(/not confirmed for Grok/)).toBeVisible();
    await section.screenshot({ path: `${SHOTS}/agents-grok-${viewport.name}.png` });

    for (const link of await page.getByRole("link").all()) {
      expect(await link.getAttribute("href") ?? "").not.toMatch(/sk_|token=|key=/);
    }
  });
}
