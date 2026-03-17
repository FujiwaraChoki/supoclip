import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [react(), tsconfigPaths()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    exclude: ["e2e/**", "playwright.config.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: [
        "src/app/api/preferences/route.ts",
        "src/app/api/tasks/route.ts",
        "src/app/api/billing/webhook/route.ts",
        "src/components/auth/sign-in.tsx",
        "src/components/auth/sign-up.tsx",
      ],
      thresholds: {
        lines: 60,
        branches: 45,
        functions: 55,
        statements: 60,
      },
    },
  },
});
