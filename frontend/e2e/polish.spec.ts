import { expect, test } from "@playwright/test";

const generation = { id: "one", user_id: "user", source_title: "A conversation worth sharing", source_type: "youtube", status: "completed", clips_count: 1, created_at: "2026-09-16T10:00:00Z", updated_at: "2026-09-16T10:00:00Z" };
const clip = { id: "clip", filename: "clip.mp4", clip_order: 1, duration: 12, start_time: "00:10", end_time: "00:22", text: "A clear idea deserves a clear explanation.", video_url: "/tasks/one/clips/clip/file", relevance_score: 0.8, virality_score: 75 };

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/get-session**", (route) => route.fulfill({ json: { session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" }, user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: true } } }));
  await page.route("**/api/fonts", (route) => route.fulfill({ json: { fonts: [] } }));
  await page.route("**/api/caption-templates", (route) => route.fulfill({ json: { templates: [{ id: "default", name: "Default", description: "Word captions" }] } }));
  await page.route("**/api/tasks/one", (route) => route.fulfill({ json: generation }));
  await page.route("**/api/tasks/one/clips", (route) => route.fulfill({ json: { clips: [clip] } }));
  await page.route("**/api/tasks/one/clips/clip/file**", (route) => route.fulfill({ status: 404 }));
});

test("full-frame framing can be selected and submitted on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route(/\/api\/tasks\/?(\?.*)?$/, route => route.fulfill({ json: { tasks: [] } }));
  await page.route("**/api/announcements**", route => route.fulfill({ json: { announcements: [] } }));
  await page.route("**/api/service-status/video-downloads", route => route.fulfill({ json: { component: { status: "operational" } } }));
  let submitted: Record<string, unknown> | undefined;
  await page.route("**/api/tasks/create", async route => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ json: { task_id: "one" } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Auto 9:16", exact: true }).click();
  const option = page.getByRole("radio", { name: /Full frame 9:16/ });
  await option.click();
  await expect(option).toHaveAttribute("aria-checked", "true");
  await page.screenshot({ path: "test-results/full-frame-framing-mobile.png" });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.keyboard.press("Escape");
  await page.getByPlaceholder("Paste a YouTube, Twitch, or Kick VOD/clip link").fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  await page.getByRole("button", { name: "Generate clips", exact: true }).click();
  await expect(page).toHaveURL(/\/tasks\/one$/);
  expect(submitted?.output_format).toBe("vertical_fit");
});


test("results send editing to the selected clip and expose one status badge", async ({ page }) => {
  await page.goto("/tasks/one");
  await expect(page.getByRole("heading", { name: generation.source_title })).toBeVisible();
  await expect(page.getByText("Completed", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Edit", exact: true })).toHaveAttribute("href", "/tasks/one/edit?clip=clip");
  await expect(page.getByPlaceholder("Start trim (sec)")).toHaveCount(0);
  await page.screenshot({ path: "test-results/results-polish.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("generation list refreshes active jobs without losing selection", async ({ page }) => {
  let reads = 0;
  await page.route(/\/api\/tasks\/?$/, (route) => route.fulfill({ json: { tasks: [{ ...generation, status: ++reads === 1 ? "processing" : "completed" }] } }));
  await page.goto("/list");
  await expect(page.getByText("Processing", { exact: true })).toBeVisible();
  await page.getByRole("checkbox").last().check();
  await expect(page.getByText("Completed", { exact: true })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("checkbox").last()).toBeChecked();
});




test("task completion retries a transient clip-read failure", async ({ page }) => {
  let taskReads = 0;
  let clipReads = 0;
  await page.route("**/api/tasks/one", route => route.fulfill({ json: { ...generation, status: ++taskReads === 1 ? "processing" : "completed" } }));
  await page.route("**/api/tasks/one/clips", route => {
    clipReads++;
    if (clipReads === 2) return route.fulfill({ status: 503, json: { detail: "Temporary clip lookup failure" } });
    return route.fulfill({ json: { clips: clipReads === 1 ? [] : [clip] } });
  });
  await page.route("**/api/tasks/one/progress", route => route.fulfill({ status: 503 }));
  await page.goto("/tasks/one");
  await expect(page.getByText("Completed", { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("link", { name: "Edit", exact: true })).toHaveAttribute("href", "/tasks/one/edit?clip=clip");
  expect(taskReads).toBeGreaterThanOrEqual(3);
  expect(clipReads).toBeGreaterThanOrEqual(3);
  await expect(page.getByRole("heading", { name: "Something went wrong" })).toHaveCount(0);
});


test("signed-out settings displays the sign-in gate without loading preferences", async ({ page }) => {
  let preferenceReads = 0;
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: null }));
  await page.route("**/api/preferences", route => {
    preferenceReads++;
    return route.fulfill({ status: 401, json: { error: "Unauthorized" } });
  });
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Sign In Required" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign In", exact: true })).toHaveAttribute("href", "/sign-in");
  await expect(page.getByRole("button", { name: /save preferences/i })).toHaveCount(0);
  expect(preferenceReads).toBe(0);
});

test("signed-in settings waits for preferences before showing the form", async ({ page }) => {
  let finish!: () => void;
  let started!: () => void;
  const pending = new Promise<void>(resolve => finish = resolve);
  const loading = new Promise<void>(resolve => started = resolve);
  await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route("**/api/preferences", async route => {
    started();
    await pending;
    await route.fulfill({ json: { fontFamily: "TikTokSans-Regular", fontSize: 24, fontColor: "#FFFFFF", notifyOnCompletion: true } });
  });
  await page.goto("/settings");
  await loading;
  await expect(page.getByRole("button", { name: /save preferences/i })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Sign In Required" })).toHaveCount(0);
  finish();
  await expect(page.getByRole("heading", { name: "Caption defaults" })).toBeVisible();
  // Nothing to save until a preference changes.
  await expect(page.getByRole("button", { name: /save preferences/i })).toHaveCount(0);
  await page.getByRole("switch").first().click();
  await expect(page.getByRole("button", { name: /save preferences/i })).toBeVisible();
});

test("font info stays open in a real selector and preserves the selected font", async ({ page }) => {
  await page.route("**/api/fonts", route => route.fulfill({ json: { fonts: [
    { name: "TikTokSans-Regular", display_name: "TikTok Sans Regular", scope: "system", supported_languages: ["en", "pt", "es", "fr", "de", "it", "pl", "tr", "vi", "el", "ru", "uk", "af"] },
    { name: "custom", display_name: "Custom Font", scope: "user", supported_languages: ["hi"] },
  ] } }));
  await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route("**/api/preferences", route => route.fulfill({ json: { fontFamily: "TikTokSans-Regular", fontSize: 24, fontColor: "#FFFFFF", notifyOnCompletion: true } }));
  await page.goto("/settings");
  await expect(page.getByRole("combobox", { name: "Language", exact: true })).toBeVisible();
  const selector = page.getByRole("combobox", { name: "Font", exact: true });
  await selector.click();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("button", { name: "Language support for TikTok Sans Regular" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "TikTok Sans Regular: language support" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("option", { name: "TikTok Sans Regular", exact: true })).toHaveAttribute("data-state", "checked");
  await page.getByRole("button", { name: "Language support for Custom Font" }).click();
  await expect(page.getByRole("heading", { name: "Custom Font: language support" })).toBeVisible();
  await expect(page.getByText(/^Supported:\s*Hindi$/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "Custom Font: language support" })).toHaveCount(0);
  await expect(page.getByRole("listbox")).toBeVisible();
  await page.getByRole("option", { name: "Custom Font", exact: true }).click();
  await expect(selector).toHaveText("Custom Font");
  await expect(page.getByRole("button", { name: /save preferences/i })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await selector.click();
  await page.getByRole("button", { name: "Language support for TikTok Sans Regular" }).click();
  await expect(page.getByRole("heading", { name: "TikTok Sans Regular: language support" })).toBeVisible();
  await page.screenshot({ path: "test-results/font-language-info.png" });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

for (const path of ["/", "/tasks/one"]) {
  test(`font info works inside caption controls at ${path}`, async ({ page }) => {
    await page.route("**/api/fonts", route => route.fulfill({ json: { fonts: [
      { name: "Inter", display_name: "Inter", scope: "system", supported_languages: ["en", "pt"] },
    ] } }));
    await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
    await page.route(/\/api\/tasks\/?(\?.*)?$/, route => route.fulfill({ json: { tasks: [] } }));
    await page.goto(path);
    if (path === "/") {
      await page.getByRole("button", { name: "Default", exact: true }).click();
    } else {
      await page.getByRole("button", { name: "More actions", exact: true }).click();
      await page.getByRole("menuitem", { name: "Restyle", exact: true }).click();
    }
    await page.getByRole("combobox").first().click();
    await page.getByRole("button", { name: "Language support for Inter" }).click();
    await expect(page.getByRole("heading", { name: "Inter: language support" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("listbox")).toBeVisible();
    await page.getByRole("option", { name: "Inter", exact: true }).click();
    await expect(page.getByRole("combobox").first()).toHaveText("Inter");
  });
}


test("signed-out pages hydrate consistently after the shared session resolves", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: null }));
  for (const path of ["/list", "/settings", "/settings/api-keys"]) {
    await page.goto(path);
    await expect(page.getByRole("link", { name: "Sign In", exact: true })).toBeVisible();
  }
  expect(errors).toEqual([]);
});


test("API keys require verification while existing keys remain manageable", async ({ page }) => {
  let verified = false;
  let created = false;
  await page.route("**/api/auth/get-session**", route => route.fulfill({ json: {
    session: { id: "session", userId: "user", expiresAt: "2099-01-01T00:00:00Z" },
    user: { id: "user", name: "Alex", email: "alex@example.com", emailVerified: verified },
  } }));
  await page.route("**/api/api-keys", route => {
    if (route.request().method() === "POST") {
      created = true;
      return route.fulfill({ json: { api_key: { id: "new", key: "sk_once-only" } } });
    }
    return route.fulfill({ json: { api_keys: [{ id: "old", name: "Existing integration", key_prefix: "sk_existing", revoked: false, created_at: null, last_used_at: null }] } });
  });
  await page.route("**/api/auth/send-verification-email", route => route.fulfill({ json: { status: true } }));
  await page.goto("/settings/api-keys");
  await expect(page.getByRole("button", { name: "Create", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Revoke", exact: true })).toBeEnabled();
  await page.screenshot({ path: "test-results/api-keys-verification-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "Send verification email" })).toBeVisible();
  await page.screenshot({ path: "test-results/api-keys-verification-mobile.png", fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Send verification email" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Verification email requested" })).toBeVisible();
  expect(created).toBe(false);
  verified = true;
  await page.getByRole("button", { name: "I have verified my email" }).click();
  await expect(page.getByRole("button", { name: "Create", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByText("sk_once-only", { exact: true })).toBeVisible();
  expect(created).toBe(true);
});

for (const width of [390, 1280]) {
  test(`clip length is remembered and sent with generation at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
    await page.route(/\/api\/tasks\/?(\?.*)?$/, route => route.fulfill({ json: { tasks: [] } }));
    await page.route("**/api/announcements**", route => route.fulfill({ json: { announcements: [] } }));
    await page.route("**/api/service-status/video-downloads", route => route.fulfill({ json: { component: { status: "operational" } } }));
    let preference = "medium";
    await page.route("**/api/preferences", async route => {
      if (route.request().method() === "PATCH") preference = route.request().postDataJSON().clipLength;
      await route.fulfill({ json: { clipLength: preference } });
    });
    let submitted: Record<string, unknown> | undefined;
    await page.route("**/api/tasks/create", async route => {
      submitted = route.request().postDataJSON();
      await route.fulfill({ json: { task_id: "one" } });
    });
    await page.goto("/");
    const picker = page.getByRole("button", { name: "Clip length", exact: true });
    await expect(picker).toContainText("Medium (30–60s)");
    await picker.click();
    await page.getByRole("radio", { name: "Long (1–2 min)", exact: true }).click();
    await expect(page.getByRole("radio", { name: "Long (1–2 min)", exact: true })).toHaveAttribute("aria-checked", "true");
    await expect.poll(() => preference).toBe("long");
    await page.screenshot({ path: `test-results/clip-length-${width}.png` });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.keyboard.press("Escape");
    await page.reload();
    await expect(picker).toContainText("Long (1–2 min)");
    await page.getByPlaceholder("Paste a YouTube, Twitch, or Kick VOD/clip link").fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
    await page.getByRole("button", { name: "Generate clips", exact: true }).click();
    await expect(page).toHaveURL(/\/tasks\/one$/);
    expect(submitted?.clip_length).toBe("long");
  });
}

for (const clipLength of ["auto", "long"]) {
  test(`desktop composer fits one row and mobile wraps with ${clipLength} clip length`, async ({ page }) => {
    await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
    await page.route(/\/api\/tasks\/?(\?.*)?$/, route => route.fulfill({ json: { tasks: [] } }));
    await page.route("**/api/announcements**", route => route.fulfill({ json: { announcements: [] } }));
    await page.route("**/api/service-status/video-downloads", route => route.fulfill({ json: { component: { status: "operational" } } }));
    await page.route("**/api/preferences", route => route.fulfill({ json: { clipLength } }));
    // Widest composer: the server offers B-roll and it is switched on.
    await page.route("**/api/broll/status", route => route.fulfill({ json: { configured: true, provider: "pexels" } }));
    await page.goto("/");
    await page.getByRole("button", { name: "B-roll", exact: true }).click();
    const submit = page.getByRole("button", { name: "Generate clips", exact: true });
    const upload = page.getByRole("button", { name: "Upload a video file", exact: true });
    await expect(page.getByRole("button", { name: "Clip length", exact: true })).toContainText(clipLength === "auto" ? "Auto length" : "Long (1–2 min)");
    for (const width of [320, 390, 768, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      for (const url of ["", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"]) {
        await page.getByLabel("YouTube, Twitch, or Kick URL", { exact: true }).fill(url);
        const buttonBox = await submit.boundingBox();
        const uploadBox = await upload.boundingBox();
        expect(buttonBox).not.toBeNull();
        expect(uploadBox).not.toBeNull();
        if (width >= 1280) {
          const cleanupBox = await page.getByRole("button", { name: /Cleanup/ }).boundingBox();
          expect(Math.abs(buttonBox!.y - uploadBox!.y)).toBeLessThanOrEqual(4);
          expect(Math.abs(cleanupBox!.y - uploadBox!.y)).toBeLessThanOrEqual(4);
          expect(buttonBox!.x).toBeGreaterThan(cleanupBox!.x + cleanupBox!.width);
        } else if (width <= 390) {
          const cleanupBox = await page.getByRole("button", { name: /Cleanup/ }).boundingBox();
          expect(cleanupBox!.y).toBeGreaterThan(uploadBox!.y);
        }
        await expect(submit).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      }
      if (width === 390 || width === 1280) {
        await page.screenshot({ path: `test-results/submit-layout-${clipLength}-${width}.png`, fullPage: true });
      }
    }
  });
}

test("captions turn off from the caption style menu and the composer stays on one row", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.route("**/api/tasks/billing-summary", route => route.fulfill({ json: { monetization_enabled: false } }));
  await page.route(/\/api\/tasks\/?(\?.*)?$/, route => route.fulfill({ json: { tasks: [] } }));
  await page.route("**/api/announcements**", route => route.fulfill({ json: { announcements: [] } }));
  await page.route("**/api/service-status/video-downloads", route => route.fulfill({ json: { component: { status: "operational" } } }));
  await page.route("**/api/preferences", route => route.fulfill({ json: { clipLength: "long" } }));
  let submitted: Record<string, unknown> | undefined;
  await page.route("**/api/tasks/create", async route => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ json: { task_id: "one" } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Default", exact: true }).click();
  await page.getByRole("switch", { name: "Captions", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "No captions", exact: true })).toBeVisible();
  await page.getByLabel("YouTube, Twitch, or Kick URL", { exact: true }).fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  const submit = page.getByRole("button", { name: "Generate clips", exact: true });
  const uploadBox = await page.getByRole("button", { name: "Upload a video file", exact: true }).boundingBox();
  expect(Math.abs((await submit.boundingBox())!.y - uploadBox!.y)).toBeLessThanOrEqual(4);
  await submit.click();
  await expect(page).toHaveURL(/\/tasks\/one$/);
  expect(submitted?.add_subtitles).toBe(false);
});
