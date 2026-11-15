import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { dueTours, FEATURE_TOURS, MAX_TOUR_STEPS } from "./feature-tours";

const BEFORE = "2026-01-01T00:00:00Z";
const AFTER = "2030-01-01T00:00:00Z";

describe("feature tours", () => {
  it("stays small enough not to overwhelm anyone", () => {
    for (const tour of FEATURE_TOURS) {
      expect(tour.steps.length).toBeGreaterThan(0);
      expect(tour.steps.length).toBeLessThanOrEqual(MAX_TOUR_STEPS);
      expect(Number.isNaN(Date.parse(tour.releasedAt))).toBe(false);
    }
    expect(new Set(FEATURE_TOURS.map((tour) => tour.id)).size).toBe(FEATURE_TOURS.length);
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

  it("doesn't repeat what an earlier tour already explained", () => {
    expect(dueTours(BEFORE, ["workflows"])).toEqual([]);
  });

  it("is for accounts older than the feature, once each", () => {
    expect(dueTours(BEFORE, []).map((tour) => tour.id)).toEqual(FEATURE_TOURS.map((tour) => tour.id));
    expect(dueTours(AFTER, [])).toEqual([]);
    expect(dueTours(BEFORE, FEATURE_TOURS.map((tour) => tour.id))).toEqual([]);
    expect(dueTours(null, [])).toEqual([]);
    expect(dueTours("nonsense", [])).toEqual([]);
  });
});
