import { defineConfig } from "@playwright/test";

const databaseUrl =
  process.env.DATABASE_URL ?? "postgresql://supoclip:supoclip_password@127.0.0.1:5432/supoclip";
const backendDatabaseUrl =
  process.env.TEST_DATABASE_URL ??
  (databaseUrl.startsWith("postgresql+asyncpg://")
    ? databaseUrl
    : databaseUrl.replace("postgresql://", "postgresql+asyncpg://"));
const backendAuthSecret = process.env.BACKEND_AUTH_SECRET ?? "supoclip_test_secret";
const frontendPort = process.env.PLAYWRIGHT_FRONTEND_PORT ?? "3100";
const backendPort = process.env.PLAYWRIGHT_BACKEND_PORT ?? "8100";
const frontendBaseUrl = `http://127.0.0.1:${frontendPort}`;
const backendBaseUrl = `http://127.0.0.1:${backendPort}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? frontendBaseUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: [
    {
      command: `cd ../backend && .venv/bin/uvicorn src.main_refactored:app --host 127.0.0.1 --port ${backendPort}`,
      url: `${backendBaseUrl}/health`,
      reuseExistingServer: !process.env.CI,
      env: {
        ...process.env,
        DATABASE_URL: backendDatabaseUrl,
        REDIS_HOST: process.env.REDIS_HOST ?? "127.0.0.1",
        REDIS_PORT: process.env.REDIS_PORT ?? "6379",
        SELF_HOST: "true",
        BACKEND_AUTH_SECRET: backendAuthSecret,
        TEMP_DIR: process.env.TEMP_DIR ?? "temp",
      },
    },
    {
      command: `npm run dev -- --hostname 127.0.0.1 --port ${frontendPort}`,
      url: frontendBaseUrl,
      reuseExistingServer: !process.env.CI,
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        NEXT_PUBLIC_APP_URL: frontendBaseUrl,
        BETTER_AUTH_URL: frontendBaseUrl,
        NEXT_PUBLIC_API_URL: backendBaseUrl,
        BACKEND_INTERNAL_URL: backendBaseUrl,
        BACKEND_AUTH_SECRET: backendAuthSecret,
        NEXT_PUBLIC_SELF_HOST: "true",
        BETTER_AUTH_SECRET:
          process.env.BETTER_AUTH_SECRET ?? "supoclip_better_auth_test_secret",
      },
    },
  ],
});
