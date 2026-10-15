import { defineConfig } from "@playwright/test";

// Explicit paid smoke. Uses an already running isolated server; never starts,
// stops or reuses the production Studio and is excluded from ordinary e2e.
export default defineConfig({
  testDir: ".", testMatch: "chat-images.live.spec.ts", timeout: 360_000, workers: 1,
  reporter: "line", outputDir: `../tmp/live-images-browser-${Date.now()}`,
  use: { baseURL: process.env.CREATOROS_IMAGES_URL ?? "http://127.0.0.1:8905", channel: "chrome", viewport: { width: 1440, height: 900 },
    permissions: ["clipboard-read", "clipboard-write"], screenshot: "only-on-failure", trace: "retain-on-failure" },
});
