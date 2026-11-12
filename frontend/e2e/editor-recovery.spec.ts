import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { draft } from "../src/lib/editor/document.test-fixture";

/**
 * Recovery paths in the clip studio: a session whose editing copy lost part
 * of the clip, and a browser whose storage for SupoClip is full.
 */
const generation = { id: "one", user_id: "user", source_title: "A conversation worth sharing", status: "completed", source_type: "youtube", clips_count: 1 };
const clips = [{ id: "clip1", filename: "clip1.mp4", clip_order: 1, duration: 36.5, text: "Hello world today", video_url: "/tasks/one/clips/clip1/file" }];
const mediaPath = path.resolve("test-results/recovery-source.mp4");
const shots = path.resolve("test-results/editor-recovery");
/** Screenshot once the dialog's open animation has finished. */
async function capture(page: Page, name: string) {
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== "running"));
  await page.screenshot({ path: path.join(shots, name) });
}

test.beforeAll(() => {
  mkdirSync(shots, { recursive: true });
  execFileSync("ffmpeg", [
    "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=3",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", mediaPath,
  ]);
});

async function setup(page: Page, truncated: boolean) {
  const state = {
    status: "ready" as "ready" | "preparing",
    revision: 6,
    duration: truncated ? 9.07 : 3,
    document: { ...draft, broll: [] },
    rebuilt: null as null | { revision: number },
  };
  await page.route("**/api/auth/get-session**", (r) => r.fulfill({ json: {
    session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" },
    user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true },
  } }));
  await page.route("**/api/tasks/one", (r) => r.fulfill({ json: generation }));
  await page.route("**/api/tasks/one/clips", (r) => r.fulfill({ json: { clips } }));
  await page.route("**/api/fonts/*", (r) => r.fulfill({
    body: readFileSync(path.resolve("../backend/fonts", path.basename(new URL(r.request().url()).pathname) + ".ttf")),
    contentType: "font/ttf",
  }));
  await page.route(/\/api\/tasks\/one\/clips\/clip1\/editor\/rebuild$/, (r) => {
    expect(r.request().postDataJSON()).toEqual({ revision: state.revision });
    state.rebuilt = { revision: state.revision };
    state.revision += 1;
    state.status = "preparing";
    // The worker finishes the fresh copy a moment later.
    setTimeout(() => {
      state.status = "ready";
      state.duration = 3;
    }, 1200);
    return r.fulfill({ status: 202, json: { status: "preparing" } });
  });
  await page.route(/\/api\/tasks\/one\/clips\/clip1\/editor\/backup$/, (r) =>
    r.fulfill({ json: { clip: "clip1", basis: "clip1.mp4", revision: 6, document: draft } }),
  );
  await page.route(/\/api\/tasks\/one\/clips\/clip1\/editor$/, async (r) => {
    if (r.request().method() === "PATCH") {
      const body = r.request().postDataJSON();
      state.revision += 1;
      state.document = body.document;
      return r.fulfill({ json: { revision: state.revision } });
    }
    if (state.status === "preparing") return r.fulfill({ json: { status: "preparing", basis: "clip1.mp4", jobs: [] } });
    return r.fulfill({ json: {
      status: "ready", basis: "clip1.mp4", duration: state.duration, width: 640, height: 360, fps: 24, hasAudio: false,
      waveform: Array.from({ length: 160 }, (_, i) => 0.2 + Math.abs(Math.sin(i)) * 0.6),
      draft: { revision: state.revision, document: state.document }, original: draft, jobs: [],
      ...(state.duration < 30 && truncated && !state.rebuilt ? { sourceIncomplete: { duration: 9.07, expected: 36.42 } } : {}),
      ...(state.rebuilt ? { draftBackup: { revision: state.rebuilt.revision, duration: 9.07, savedAt: "2026-11-12T13:00:00Z" } } : {}),
    } });
  });
  await page.route(/\/editor\/media\/(clean|layout)\.mp4$/, (r) => r.fulfill({ body: readFileSync(mediaPath), contentType: "video/mp4" }));
  await page.route("**/editor/media/thumbnails.jpg", (r) => r.fulfill({ status: 404 }));
  await page.goto("/tasks/one/edit?clip=clip1");
  await expect(page.getByLabel("Word 1", { exact: true })).toBeVisible();
  return state;
}

test("a truncated editing copy is explained and rebuilt after confirmation", async ({ page }) => {
  const state = await setup(page, true);
  const alert = page.getByTestId("source-incomplete");
  await expect(alert).toContainText("only 9 sec long instead of 36 sec");
  await capture(page, "1-truncated-copy.png");

  await alert.getByRole("button", { name: "Rebuild editing copy" }).click();
  const dialog = page.getByRole("dialog", { name: "Rebuild the editing copy?" });
  await expect(dialog).toContainText("don't carry over");
  await capture(page, "2-rebuild-confirmation.png");
  await dialog.getByRole("button", { name: "Rebuild" }).click();

  await expect(page.getByTestId("source-incomplete")).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByLabel("Word 1", { exact: true })).toBeVisible();
  expect(state.rebuilt).toEqual({ revision: 6 });

  await page.getByRole("button", { name: "More", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("menuitem", { name: "Download previous draft" }).click();
  expect((await download).suggestedFilename()).toBe("supoclip-previous-draft-clip1.json");
});

test("a full browser storage explains itself and frees only saved copies", async ({ page }) => {
  // Another clip's unsaved changes, and an old full-history copy the server already has.
  await page.addInitScript(([doc]) => {
    const old = JSON.stringify(doc);
    localStorage.setItem("supoclip-editor:/api/tasks/old/clips/a/editor", JSON.stringify({
      basis: "a.mp4", revision: 2, saved: old, history: { past: Array.from({ length: 50 }, () => doc), present: doc, future: [] },
    }));
    localStorage.setItem("supoclip-editor:/api/tasks/old/clips/b/editor", JSON.stringify({
      v: 2, basis: "b.mp4", revision: 2, history: { past: [], present: { ...doc, volume: 0.4 }, future: [] },
    }));
    // From now on the browser refuses new studio backups, as a full quota does.
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key: string, value: string) {
      if (key === "supoclip-editor:/api/tasks/one/clips/clip1/editor") throw new DOMException("full", "QuotaExceededError");
      return setItem.call(this, key, value);
    };
  }, [draft]);
  let release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  await setup(page, false);
  // Hold the save so the edit stays unsaved while the banner is checked.
  await page.route(/\/api\/tasks\/one\/clips\/clip1\/editor$/, async (r) => {
    if (r.request().method() !== "PATCH") return r.fallback();
    await held;
    return r.fallback();
  });
  await page.getByLabel("Word 1", { exact: true }).fill("Changed");
  await expect(page.getByText("This browser's storage for SupoClip is full")).toBeVisible();
  await capture(page, "3-storage-full.png");

  // Before warning, the studio already freed the copy the server has, and
  // kept the other clip's unsaved changes.
  expect(await page.evaluate(() => localStorage.getItem("supoclip-editor:/api/tasks/old/clips/a/editor"))).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem("supoclip-editor:/api/tasks/old/clips/b/editor"))).not.toBeNull();

  await page.getByRole("button", { name: "See what's stored" }).click();
  const dialog = page.getByRole("dialog", { name: "Browser backups of your drafts" });
  await expect(dialog).toContainText("Deleting projects doesn't change this storage");
  const summary = dialog.getByTestId("browser-backups-summary");
  await expect(summary).toContainText("1 clip has a browser backup");
  await expect(summary).toContainText("1 has changes that aren't in your account yet");
  await expect(dialog.getByRole("button", { name: "Remove saved copies" })).toBeDisabled();
  await capture(page, "4-browser-backups.png");
  release();
  await dialog.getByRole("button", { name: "Done" }).click();
  await expect(page.getByRole("status").filter({ hasText: "All changes saved" })).toBeVisible();
  // Saved to the account: no backup is needed, so the warning goes away.
  await expect(page.getByText("This browser's storage for SupoClip is full")).toHaveCount(0);
});

test("browser backups can be reviewed and saved copies removed from the menu", async ({ page }) => {
  await page.addInitScript(([doc]) => {
    for (const clip of ["a", "b"])
      localStorage.setItem(`supoclip-editor:/api/tasks/old/clips/${clip}/editor`, JSON.stringify({
        v: 2, basis: `${clip}.mp4`, revision: 2, unsaved: clip === "b", at: 1,
        history: { past: [], present: { ...doc, volume: clip === "b" ? 0.4 : 1 }, future: [] },
      }));
  }, [draft]);
  await setup(page, false);
  await page.getByRole("button", { name: "More", exact: true }).click();
  await page.getByRole("menuitem", { name: "Browser backups" }).click();
  const dialog = page.getByRole("dialog", { name: "Browser backups of your drafts" });
  const summary = dialog.getByTestId("browser-backups-summary");
  await expect(summary).toContainText("3 clips have a browser backup");
  await expect(summary).toContainText("1 has changes that aren't in your account yet");
  await dialog.getByRole("button", { name: "Remove saved copies" }).click();
  await expect(summary).toContainText("1 clip has a browser backup");
  expect(await page.evaluate(() => localStorage.getItem("supoclip-editor:/api/tasks/old/clips/b/editor"))).not.toBeNull();
});
