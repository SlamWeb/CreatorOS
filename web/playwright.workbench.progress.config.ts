import { defineConfig } from "@playwright/test";

// Free transport-fault regression: local HTTP only, no CreatorOS or model server.
export default defineConfig({
  testDir: "./workbench-progress-tests", timeout: 15_000, workers: 1, retries: 0,
  reporter: "line", outputDir: "./tmp/workbench-progress-smoke", use: { trace: "on" },
});
