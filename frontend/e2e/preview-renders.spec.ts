import { existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Demo media from a local checkout (SUPOCLIP_DEMO_CLIPS); any small MP4s work.
const DEMO = process.env.SUPOCLIP_DEMO_CLIPS ?? "";
const MEDIA_DIR = process.env.PREVIEW_RENDERS_MEDIA_DIR;
const files = ["demo_clip_1_0415-0432_49873660.mp4", "demo_clip_2_0607-0631_78b8f7e0.mp4", "demo_clip_2_1343-1402_6bec5954.mp4", "demo_clip_1_0243-0307_0280af26.mp4"];

const task = {
  id: "demo", user_id: "user", source_id: "s", source_title: "How we priced our SaaS (founder interview)", source_type: "youtube",
  status: "completed", clips_count: 4, workflow: "highlights", created_at: "2026-11-18T10:00:00Z", updated_at: "2026-11-18T10:20:00Z",
};
const titles = ["The pricing page nobody reads", "Why we doubled our price", "One email that saved churn", "Stop discounting annual plans"];

type State = { status: string; progress: number };

function clip(index: number, state: State) {
  const ready = state.status === "ready";
  return {
    id: `clip-${index + 1}`, filename: `clip_${index + 1}.mp4`, video_url: `/tasks/demo/clips/clip-${index + 1}/file`,
    hook_title: titles[index], clip_order: index + 1, duration: 18 + index * 4, start_time: `0${index + 2}:10`, end_time: `0${index + 2}:31`,
    text: "We tested three prices in one week.", relevance_score: 0.9, reasoning: "", virality_score: 88 - index * 6,
    hook_score: 20, engagement_score: 20, value_score: 20, shareability_score: 20, hook_type: "statement", created_at: task.created_at,
    render_quality: ready ? "full" : "preview",
    full_render: ready ? { status: "ready", progress: 100, error: null } : { status: state.status, progress: state.progress, error: null },
  };
}

async function mockApp(page: Page, states: State[]) {
  await page.route("**/api/auth/get-session**", (route) => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true } } }));
  await page.route("**/api/tasks/billing-summary", (route) => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route("**/api/fonts", (route) => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", (route) => route.fulfill({ json: { templates: [] } }));
  await page.route("**/api/announcements**", (route) => route.fulfill({ json: { announcements: [] } }));
  await page.route("**/api/feature-tours", (route) => route.fulfill({ json: { createdAt: "2026-01-01T00:00:00Z", seen: ["workflows", "composer-settings"] } }));
  await page.route(/\/api\/tasks\/?(\?.*)?$/, (route) => route.fulfill({ json: { tasks: [task] } }));
  await page.route("**/api/tasks/demo", (route) => route.fulfill({ json: { ...task, clips: states.map((state, i) => clip(i, state)) } }));
  await page.route("**/api/tasks/demo/clips", (route) => route.fulfill({ json: { clips: states.map((state, i) => clip(i, state)) } }));
  await page.route("**/api/tasks/demo/progress", (route) => route.fulfill({ status: 503 }));
  await page.route(/\/api\/tasks\/demo\/clips\/clip-(\d)\/file/, (route) => {
    const index = Number(/clip-(\d)/.exec(route.request().url())![1]) - 1;
    const path = join(DEMO, files[index]);
    return existsSync(path) ? route.fulfill({ path, contentType: "video/mp4" }) : route.fulfill({ status: 404 });
  });
}

test.skip(!DEMO, "Set SUPOCLIP_DEMO_CLIPS to a folder with demo clips");
test.use({ viewport: { width: 1280, height: 860 }, ...(MEDIA_DIR ? { video: { mode: "on", size: { width: 1280, height: 860 } } } : {}) });

test("preview clips say so, and a download waits for the full-quality render", async ({ page }) => {
  const states: State[] = files.map(() => ({ status: "none", progress: 0 }));
  await mockApp(page, states);
  const polls = [25, 55, 85];
  const calls: string[] = [];
  await page.route("**/api/tasks/demo/clips/clip-1/full-render", async (route) => {
    calls.push(route.request().method());
    if (route.request().method() === "POST") {
      states[0] = { status: "queued", progress: 0 };
    } else {
      const next = polls.shift();
      states[0] = next === undefined ? { status: "ready", progress: 100 } : { status: "running", progress: next };
    }
    await route.fulfill({ json: { clip_id: "clip-1", ...clip(0, states[0]) } });
  });

  await page.goto("/tasks/demo");
  await expect(page.getByText("Preview quality: you get the full-quality clip when you export or publish it.").first()).toBeVisible();
  await expect(page.getByText("Preview", { exact: true })).toHaveCount(4);
  if (MEDIA_DIR) mkdirSync(MEDIA_DIR, { recursive: true });

  // The tour explains it the first time.
  const tour = page.getByText("Your clips are ready sooner");
  await expect(tour).toBeVisible({ timeout: 10_000 });
  await page.waitForTimeout(800);
  if (MEDIA_DIR) await page.screenshot({ path: join(MEDIA_DIR, "tour.png") });
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText("Full quality when you need it")).toBeVisible();
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "Done", exact: true }).click();
  if (MEDIA_DIR) await page.screenshot({ path: join(MEDIA_DIR, "grid.png") });

  // The clip's details say it too; its download waits for the full-quality render.
  await page.getByRole("button", { name: `Preview ${titles[0]}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Preview quality: you get the full-quality clip when you export or publish it.")).toBeVisible();
  await page.waitForTimeout(1000);
  const download = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "Download", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Preparing full quality… 55%" })).toBeVisible();
  if (MEDIA_DIR) await page.screenshot({ path: join(MEDIA_DIR, "preparing.png") });
  await download;
  await expect(dialog.getByRole("button", { name: "Download", exact: true })).toBeEnabled();
  await expect(dialog.getByText("Preview quality: you get the full-quality clip when you export or publish it.")).toHaveCount(0);
  expect(calls[0]).toBe("POST");
  await page.waitForTimeout(1200);
  await page.keyboard.press("Escape");
  await expect(page.getByText("Preview", { exact: true })).toHaveCount(3);
  await page.waitForTimeout(1200);
});
