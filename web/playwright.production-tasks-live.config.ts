import { defineConfig } from "@playwright/test";

if (!process.env.CREATOROS_TASKS_LIVE_ROOT) throw new Error("Explicit paid fixture root required");
export default defineConfig({
  testDir: "./production-tasks-live", timeout: 1_500_000, workers: 1, retries: 0,
  outputDir: "./tmp/production-tasks-live-results", reporter: "line",
  use: { baseURL: process.env.CREATOROS_TASKS_LIVE_URL ?? "http://127.0.0.1:8790", channel: "chrome", viewport: { width: 1440, height: 900 },
    actionTimeout: 15000, navigationTimeout: 30000, trace: "on", screenshot: "only-on-failure" },
});
