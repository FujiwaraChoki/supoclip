import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { dueTours, FEATURE_TOURS, MAX_ONBOARDING_STEPS, MAX_TOUR_STEPS, maxSteps } from "./feature-tours";

const BEFORE = "2026-01-01T00:00:00Z";
const AFTER = "2030-01-01T00:00:00Z";

describe("feature tours", () => {
  it("stays small enough not to overwhelm anyone", () => {
    for (const tour of FEATURE_TOURS) {
      expect(tour.steps.length).toBeGreaterThan(0);
      expect(tour.steps.length).toBeLessThanOrEqual(maxSteps(tour));
      expect(maxSteps(tour)).toBe(tour.audience === "new" ? MAX_ONBOARDING_STEPS : MAX_TOUR_STEPS);
      expect(Number.isNaN(Date.parse(tour.releasedAt))).toBe(false);
    }
    expect(new Set(FEATURE_TOURS.map((tour) => tour.id)).size).toBe(FEATURE_TOURS.length);
  });

  it("ships announcement media as a WebM, an MP4 and a WebP poster that exist and stay small", () => {
    const pub = join(process.cwd(), "public");
    for (const tour of FEATURE_TOURS) {
      if (!tour.media) continue;
      expect(tour.media.poster, tour.id).toMatch(/\.webp$/);
      expect(tour.media.videos.map((video) => video.type), tour.id).toEqual(["video/webm", "video/mp4"]);
      for (const src of [tour.media.poster, ...tour.media.videos.map((video) => video.src)]) {
        const size = statSync(join(pub, src)).size;
        expect(size, src).toBeGreaterThan(0);
        expect(size, src).toBeLessThan(2 * 1024 * 1024);
      }
    }
  });

  it("has the words for every step in every language", () => {
    const root = join(process.cwd(), "messages");
    for (const locale of readdirSync(root)) {
      const messages = JSON.parse(readFileSync(join(root, locale, "tours.json"), "utf8"));
      for (const tour of FEATURE_TOURS) for (const step of tour.steps) {
        expect(messages[tour.id][step.message].title, `${locale} ${tour.id}`).toBeTruthy();
        expect(messages[tour.id][step.message].body, `${locale} ${tour.id}`).toBeTruthy();
      }
    }
  });

  it("welcomes accounts created since onboarding shipped, first and once", () => {
    const welcome = FEATURE_TOURS.find((tour) => tour.id === "welcome")!;
    expect(FEATURE_TOURS[0]).toBe(welcome);
    // Welcome comes first; the only other tours such an account is due are for features released after it signed up.
    const due = dueTours(welcome.releasedAt, []);
    expect(due[0]).toBe(welcome);
    expect(due.slice(1).every((tour) => tour.releasedAt > welcome.releasedAt)).toBe(true);
    expect(dueTours(BEFORE, []).map((tour) => tour.id)).not.toContain("welcome");
    expect(dueTours(AFTER, ["welcome"])).toEqual([]);
  });

  it("walks through every page and ends back on the create screen", () => {
    const welcome = FEATURE_TOURS.find((tour) => tour.id === "welcome")!;
    expect(welcome.steps.every((step) => step.path?.startsWith("/"))).toBe(true);
    expect(new Set(welcome.steps.map((step) => step.path))).toEqual(new Set(["/", "/list", "/settings/social", "/analytics", "/settings/api-keys", "/settings"]));
    expect(welcome.steps.at(-1)!.path).toBe("/");
    // A feature tour stays on its page.
    for (const tour of FEATURE_TOURS.filter((entry) => entry.audience !== "new")) expect(tour.steps.every((step) => step.path === undefined)).toBe(true);
  });

  it("doesn't repeat what an earlier tour already explained", () => {
    expect(dueTours(BEFORE, ["workflows"]).map((tour) => tour.id)).not.toContain("composer-settings");
    expect(dueTours(BEFORE, ["dynamic-edits"]).map((tour) => tour.id)).not.toContain("composer-settings");
    for (const tour of FEATURE_TOURS) {
      const messages = tour.steps.map((step) => step.message);
      expect(new Set(messages).size, tour.id).toBe(messages.length);
      // Only the opening question may share its target with the step after it. The welcome tour walks the pages
      // and ends back where it started, so it points at the composer twice.
      if (tour.audience === "new") continue;
      const targets = tour.steps.filter((step) => !step.intro).map((step) => step.target);
      expect(new Set(targets).size, tour.id).toBe(targets.length);
    }
  });

  it("keeps the dynamic edits tours behind their flag", () => {
    // The host drops them unless /api/feature-flags says dynamic_edits is true (feature-tour.openers.test.tsx).
    const gated = FEATURE_TOURS.filter((tour) => tour.id.startsWith("dynamic-edits"));
    expect(gated.map((tour) => tour.id)).toEqual(["dynamic-edits", "dynamic-edits-studio"]);
    for (const tour of gated) expect(tour.requiresFlag, tour.id).toBe("dynamic_edits");
  });

  it("opens every tour that has more than one step with Show me / Not now", () => {
    for (const tour of FEATURE_TOURS) {
      if (tour.steps.length < 2) continue;
      expect(tour.steps[0].intro, tour.id).toBe(true);
      expect(tour.steps.filter((step) => step.intro), tour.id).toHaveLength(1);
    }
  });

  it("points only at targets the page puts on screen or can open", () => {
    const components = join(process.cwd(), "src", "components");
    // Components and the pages themselves: the welcome tour points at page headers.
    const files = [components, join(components, "..", "app")].flatMap((dir) =>
      (readdirSync(dir, { recursive: true }) as string[])
        .filter((file) => file.endsWith(".tsx") && !file.includes(".test."))
        .map((file) => readFileSync(join(dir, file), "utf8")));
    const rendered = (target: string) =>
      files.some((text) => text.includes(`data-tour="${target}"`)) ||
      (target.startsWith("workflow-") && files.some((text) => text.includes("data-tour={`workflow-${id}`}")));
    // Targets that only exist once a closed menu or tab is open, and the page that can open it.
    const hidden: Record<string, { opener: "settingsSection" | "inspectorTab"; value: string }> = {
      "edit-intensity": { opener: "settingsSection", value: "edits" },
      "edit-options": { opener: "settingsSection", value: "edits" },
      "caption-styles": { opener: "settingsSection", value: "captions" },
      "studio-edits-tab": { opener: "inspectorTab", value: "edits" },
    };
    const hosts = {
      // The create screen hands its opener to the app shell, which hosts the tours.
      settingsSection: ["home-app.tsx", "onSettingsSection:"],
      inspectorTab: [join("editor", "editor-workspace.tsx"), "onInspectorTab="],
    } as const;
    // The welcome tour's targets are set through props on page sections; its own tests walk the pages.
    for (const tour of FEATURE_TOURS.filter((entry) => entry.audience !== "new")) for (const step of tour.steps) {
      expect(rendered(step.target), `${tour.id}: nothing renders data-tour="${step.target}"`).toBe(true);
      const where = hidden[step.target];
      if (!where) continue;
      expect(step[where.opener], `${tour.id} ${step.target}`).toBe(where.value);
      const [host, prop] = hosts[where.opener];
      expect(readFileSync(join(components, host), "utf8"), `${host} passes ${prop}`).toContain(prop);
    }
  });

  it("is for accounts older than the feature, once each", () => {
    const features = FEATURE_TOURS.filter((tour) => tour.audience !== "new").map((tour) => tour.id);
    expect(dueTours(BEFORE, []).map((tour) => tour.id)).toEqual(features);
    expect(dueTours(AFTER, []).map((tour) => tour.id)).toEqual(["welcome"]);
    expect(dueTours(BEFORE, FEATURE_TOURS.map((tour) => tour.id))).toEqual([]);
    expect(dueTours(null, [])).toEqual([]);
    expect(dueTours("nonsense", [])).toEqual([]);
  });
});
