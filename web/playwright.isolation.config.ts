import { defineConfig } from "@playwright/test";

if (!process.env.CREATOROS_ISOLATION_URL || !process.env.CREATOROS_ISOLATION_OTHER_URL
    || !process.env.CREATOROS_ISOLATION_RESULT) throw new Error("Explicit isolated stack URLs and result directory required");

export default defineConfig({
  testDir: "./isolation-tests", workers: 1, retries: 0, reporter: "line", timeout: 90_000,
  outputDir: process.env.CREATOROS_ISOLATION_RESULT,
  use: { baseURL: process.env.CREATOROS_ISOLATION_URL, channel: "chrome", viewport: { width: 1440, height: 900 } },
});
