import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// Opt-in: requires the real worker and locally configured media/LLM providers.
export default defineConfig(base, {
  testMatch: "local-workflow.spec.ts",
  testIgnore: [],
  timeout: 180_000,
  expect: { timeout: 15_000 },
  workers: 1,
  use: { video: "off" },
});
