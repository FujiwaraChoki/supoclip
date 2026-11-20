import fs from "fs";
import path from "path";
import { randomUUID } from "crypto";

import { expect, test, type Page } from "@playwright/test";

import { PrismaClient } from "../src/generated/prisma";

// Demo data only. RECORD_PR_MEDIA=1 keeps the video for the pull request; screenshots go to
// test-results/feedback-inbox. Saving a webhook resolves its host, so this needs DNS (docs/feedback.md).
test.use({
  video: { mode: process.env.RECORD_PR_MEDIA ? "on" : "retain-on-failure", size: { width: 1280, height: 800 } },
  viewport: { width: 1280, height: 800 },
});

const seed = JSON.parse(fs.readFileSync(path.join(process.cwd(), "e2e", ".seed.json"), "utf8"));
const shots = path.join(process.cwd(), "test-results", "feedback-inbox");
const DEMO_USERS = [
  { name: "Maya Chen", email: "maya.chen@example.com" },
  { name: "Jonas Weber", email: "jonas@example.org" },
  { name: "Priya Patel", email: "priya.p@example.net" },
];

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000);

async function seedFeedback(prisma: PrismaClient) {
  for (const table of ["feedback_webhook_attempts", "feedback_webhook_deliveries", "feedback_notes", "feedback_submissions", "feedback_webhooks"]) {
    await prisma.$executeRawUnsafe(`DELETE FROM ${table}`);
  }
  await prisma.user.deleteMany({ where: { email: { in: DEMO_USERS.map((user) => user.email) } } });
  const users = [];
  for (const demo of DEMO_USERS) users.push(await prisma.user.create({ data: { ...demo, is_admin: false } }));
  const [maya, jonas, priya] = users;

  const rows: [typeof maya | null, string, string, string, string, number, string | null][] = [
    [maya, "bug", "Captions drift about half a second behind the voice after the 10-minute mark on long podcasts. Happens on every export from the studio.", "open", "web", 12, "/tasks/demo-task/edit"],
    [jonas, "sales", "We're a team of 6 editors at a small agency. Is there a team plan with shared billing and one invoice?", "open", "web", 47, "/pricing"],
    [priya, "feature", "Would love a way to pick the hook title font separately from the caption font.", "in_progress", "ios", 180, null],
    [maya, "cancellation", "Pausing for the summer, will be back in September.", "resolved", "web", 60 * 26, "/settings"],
    [jonas, "general", "The new studio timeline is great. Splitting clips is much faster now.", "resolved", "web", 60 * 30, "/tasks/demo-task"],
    [priya, "bug", "Upload stalls at 99% on hotel Wi-Fi, retrying works.", "open", "ios", 60 * 50, null],
  ];
  const ids: string[] = [];
  for (const [user, category, message, status, platform, age, page] of rows) {
    const id = randomUUID();
    ids.push(id);
    await prisma.$executeRaw`
      INSERT INTO feedback_submissions (id, user_id, email, category, message, status, platform, page_url, locale,
        user_agent, app_version, resolved_at, created_at, updated_at)
      VALUES (${id}, ${user?.id ?? null}, ${user?.email ?? null}, ${category}, ${message}, ${status}, ${platform}, ${page}, 'en',
        ${platform === "ios" ? "SupoClip/2.4 CFNetwork Darwin" : "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) Chrome/131.0"},
        ${platform === "ios" ? "2.4.0" : null}, ${status === "resolved" ? minutesAgo(age - 30) : null},
        ${minutesAgo(age)}, ${minutesAgo(age)})`;
  }
  await prisma.$executeRaw`
    INSERT INTO feedback_notes (id, submission_id, author_id, body, created_at)
    VALUES (${randomUUID()}, ${ids[2]}, ${null}, 'Design is looking at this for the next caption template pass.', ${minutesAgo(90)})`;
  return ids;
}

async function seedDeliveries(prisma: PrismaClient, webhookId: string, submissionIds: string[]) {
  const plan: [number, string, number, (number | null)[]][] = [
    [0, "delivered", 1, [204]],
    [1, "failed", 1, [404]],
    [5, "pending", 3, [502, 502, 503]],
  ];
  for (const [index, status, attempts, codes] of plan) {
    const id = randomUUID();
    await prisma.$executeRaw`
      INSERT INTO feedback_webhook_deliveries (id, submission_id, webhook_id, status, attempts, next_attempt_at,
        last_status_code, last_error, delivered_at, created_at, updated_at)
      VALUES (${id}, ${submissionIds[index]}, ${webhookId}, ${status}, ${attempts}, ${new Date(Date.now() + 9 * 60_000)},
        ${codes[codes.length - 1]}, ${status === "delivered" ? null : `HTTP ${codes[codes.length - 1]}`},
        ${status === "delivered" ? minutesAgo(10) : null}, ${minutesAgo(12)}, ${minutesAgo(5)})`;
    for (const [attempt, code] of codes.entries()) {
      await prisma.$executeRaw`
        INSERT INTO feedback_webhook_attempts (delivery_id, attempt, status_code, error, duration_ms, attempted_at)
        VALUES (${id}, ${attempt + 1}, ${code}, ${code && code >= 300 ? `HTTP ${code}` : null}, 180, ${minutesAgo(12 - attempt * 2)})`;
    }
  }
}

async function signIn(page: Page) {
  await page.goto("/sign-in");
  await page.getByPlaceholder("Email").fill(seed.admin.email);
  await page.getByPlaceholder("Password").fill(seed.admin.password);
  await page.getByRole("button", { name: "Sign In" }).click();
  await page.waitForURL("**/");
}

// The dev server compiles each admin route on its first visit, which took over 15 s in CI.
const firstVisit = { timeout: 60_000 };

test("admins triage feedback and manage its webhooks", async ({ page }) => {
  test.setTimeout(240_000);
  const prisma = new PrismaClient();
  const ids = await seedFeedback(prisma);
  await signIn(page);

  await page.goto("/admin");
  await page.getByRole("link", { name: "Feedback inbox" }).click();
  await expect(page.getByRole("heading", { name: "Feedback inbox" })).toBeVisible(firstVisit);
  await expect(page.getByText(/Captions drift/)).toBeVisible();
  await page.screenshot({ path: `${shots}/inbox.png`, fullPage: true });

  await page.getByLabel("Category").selectOption("bug");
  await page.getByRole("button", { name: "Apply" }).click();
  await page.waitForURL(/category=bug/);
  await expect(page.getByText(/Upload stalls/)).toBeVisible();
  await expect(page.getByText(/team plan/)).toHaveCount(0);
  await page.getByLabel("Email, name or user ID").fill("maya");
  await page.getByRole("button", { name: "Apply" }).click();
  await page.waitForURL(/q=maya/);
  await expect(page.getByRole("link", { name: /Captions drift/ })).toBeVisible();
  await expect(page.getByText(/Upload stalls/)).toHaveCount(0);
  await page.screenshot({ path: `${shots}/inbox-filtered.png`, fullPage: true });

  await page.getByRole("link", { name: /Captions drift/ }).click();
  await expect(page.getByRole("heading", { name: "Bug report" })).toBeVisible(firstVisit);
  await page.getByRole("button", { name: "In progress" }).click();
  await expect(page.getByText("Marked as In progress.")).toBeVisible();
  await page.getByLabel("Add a note").fill("Reproduced on a 45-minute upload. Fix is in review.");
  await page.getByRole("button", { name: "Add note" }).click();
  await expect(page.getByText("Note added.")).toBeVisible();
  await page.screenshot({ path: `${shots}/detail.png`, fullPage: true });

  await page.goto("/admin/feedback/webhooks");
  await expect(page.getByRole("heading", { name: "Feedback webhooks" })).toBeVisible(firstVisit);
  await page.getByRole("button", { name: "Add webhook" }).click();
  await page.getByLabel("Name").fill("Internal tools");
  await page.getByLabel(/JSON \(signed\)/).check();
  await page.getByLabel(/Webhook URL/).fill("https://169.254.169.254/latest/meta-data");
  await page.getByRole("button", { name: "Save webhook" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "public internet address" })).toBeVisible();
  await page.screenshot({ path: `${shots}/webhook-refused.png` });

  await page.getByLabel(/Webhook URL/).fill("https://example.com/hooks/supoclip-demo");
  await page.getByLabel("Cancellation reason").uncheck();
  await page.getByRole("button", { name: "Save webhook" }).click();
  await expect(page.getByText(/Signing secret for Internal tools/)).toBeVisible();
  await page.screenshot({ path: `${shots}/webhook-secret.png` });
  await page.getByRole("button", { name: "Done" }).click();

  await page.getByRole("button", { name: "Add webhook" }).click();
  await page.getByLabel("Name").fill("Team Discord");
  await page.getByLabel(/Webhook URL/).fill("https://discord.com/api/webhooks/1234567890/demo-token-k3Yz");
  await page.getByRole("button", { name: "Save webhook" }).click();
  await expect(page.getByText("Webhook added.")).toBeVisible();

  const [discord] = await prisma.$queryRaw<{ id: string }[]>`SELECT id FROM feedback_webhooks WHERE name = 'Team Discord'`;
  await seedDeliveries(prisma, discord.id, ids);
  await page.reload();
  const card = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: "Team Discord" }) });
  await card.getByRole("button", { name: "Deliveries" }).click();
  await expect(card.getByText("HTTP 502 → HTTP 502 → HTTP 503")).toBeVisible();
  await expect(page.getByText("demo-token")).toHaveCount(0);
  await page.screenshot({ path: `${shots}/webhooks.png`, fullPage: true });

  // The Hermes assistant is configured on the server (HERMES_FEEDBACK_WEBHOOK_*); the row stands in for an API start.
  const hermesId = randomUUID();
  await prisma.$executeRaw`
    INSERT INTO feedback_webhooks (id, name, kind, url_encrypted, url_hint, secret_encrypted, categories, enabled, env_source, created_at)
    VALUES (${hermesId}, 'Hermes assistant', 'hermes', 'not-a-real-url', '', 'not-a-real-secret',
      ARRAY['bug', 'feature', 'general', 'sales', 'cancellation'], TRUE, 'HERMES_FEEDBACK_WEBHOOK_URL', ${minutesAgo(1)})`;
  await seedDeliveries(prisma, hermesId, ids);
  await page.reload();
  const hermes = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: "Hermes assistant" }) });
  await expect(hermes.getByText("Configured on the server. Receives every category.")).toBeVisible();
  await expect(hermes.getByRole("button", { name: "Edit" })).toHaveCount(0);
  await expect(hermes.getByRole("button", { name: "Delete" })).toHaveCount(0);
  await expect(hermes.getByRole("button", { name: "Send test" })).toBeVisible();
  await hermes.getByRole("button", { name: "Deliveries" }).click();
  await expect(hermes.getByText("HTTP 502 → HTTP 502 → HTTP 503")).toBeVisible();
  await hermes.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${shots}/webhooks-hermes.png`, fullPage: true });
  await hermes.getByRole("button", { name: "Hide deliveries" }).click();

  await page.getByRole("switch", { name: "Send feedback to Internal tools" }).click();
  await expect(page.getByText(/Internal tools is off/)).toBeVisible();
  await page.screenshot({ path: `${shots}/webhooks-disabled.png`, fullPage: true });
  await prisma.$disconnect();
});
