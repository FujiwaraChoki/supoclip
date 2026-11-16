import { defineConfig } from "vitest/config";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [tsconfigPaths()],
  test: {
    environment: "node",
    include: ["tests/billing-account*.test.ts"],
    fileParallelism: false,
    testTimeout: 15000,
  },
});
