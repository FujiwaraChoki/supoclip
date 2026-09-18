import fs from "fs";
import path from "path";

import { expect, test } from "@playwright/test";

const seed = JSON.parse(
  fs.readFileSync(path.join(process.cwd(), "e2e", ".seed.json"), "utf8"),
);

async function signIn(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/sign-in");
  await page.getByPlaceholder("Email").fill(email);
  await page.getByPlaceholder("Password").fill(password);
  await page.getByRole("button", { name: "Sign In" }).click();
  await page.waitForURL("**/");
}

test("regular user can browse seeded tasks and save preferences", async ({ page }) => {
  // This flow compiles several routes on its first visit to the dev server.
  test.setTimeout(60_000);
  await signIn(page, seed.regular.email, seed.regular.password);

  await page.goto("/list");
  await expect(page.getByText(seed.completedSourceTitle)).toBeVisible();

  await page.goto(`/tasks/${seed.completedTaskId}`);
  await expect(page.getByText("This is a seeded clip")).toBeVisible();

  await page.goto("/settings");
  await page.getByRole("button", { name: /save preferences/i }).click();
  await expect(page.getByText(/preferences saved/i)).toBeVisible();

  await page.goto("/admin");
  await expect(page.getByText(/not an admin/i)).toBeVisible();
});

test("admin user can access the admin dashboard", async ({ page }) => {
  await signIn(page, seed.admin.email, seed.admin.password);

  await page.goto("/admin");
  await expect(page.getByText(/admin dashboard/i)).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /currently processing tasks/i }),
  ).toBeVisible();
});

test("social publishing explains missing configuration without breaking task pages", async ({ page }) => {
  test.setTimeout(60_000);
  await signIn(page, seed.regular.email, seed.regular.password);
  await page.goto(`/tasks/${seed.completedTaskId}`);
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(page.getByText("Social publishing is not configured on this server.", { exact: false })).toBeVisible();
  await page.getByRole("link", { name: "Social Accounts", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Social Accounts", exact: true })).toBeVisible();
  await expect(page.getByText("Not configured on this server.", { exact: true })).toHaveCount(3);
  await expect(page.getByText("No accounts connected yet.", { exact: true })).toBeVisible();
  await expect(page.getByText("Failed to load social accounts")).toHaveCount(0);
  await page.screenshot({ path: "test-results/social-accounts-unconfigured.png", fullPage: true });
});
