import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: ["polish.spec.ts", "editor-studio.spec.ts"],
  fullyParallel: false,
  use: {
    baseURL: "http://127.0.0.1:3118", screenshot: "only-on-failure", trace: "retain-on-failure",
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH },
  },
  webServer: {
    command: "pnpm exec next dev --hostname 127.0.0.1 --port 3118",
    url: "http://127.0.0.1:3118",
    reuseExistingServer: !process.env.CI,
    env: {
      NEXT_BUILD_DIR: ".next-polish",
      NEXT_PUBLIC_SELF_HOST: "true", NEXT_PUBLIC_APP_URL: "http://127.0.0.1:3118",
      BETTER_AUTH_URL: "http://127.0.0.1:3118",
      BETTER_AUTH_SECRET: "supoclip-isolated-browser-test-secret",
      DATABASE_URL: "postgresql://unused:unused@127.0.0.1:1/unused",
    },
  },
});
