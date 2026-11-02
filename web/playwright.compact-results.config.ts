import { defineConfig } from "@playwright/test";
import path from "node:path";
import { isolatedBrowserRun } from "./playwright.isolated";

const repo = path.resolve(import.meta.dirname, "..");
const run = isolatedBrowserRun("compact-results", 8789);
const python = process.env.CREATOROS_PYTHON ?? "python";

// Isolated HTTP/SQLite, controlled chat and local producer. No paid model calls.
export default defineConfig({
  testDir: "./e2e", testMatch: ["compact-chat.spec.ts", "single-task-preview.spec.ts", "agent-transcript.spec.ts"],
  timeout: 90_000, workers: 1, reporter: "line",
  outputDir: run.output,
  use: { baseURL: run.url, channel: "chrome", viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: {
    command: `"${python}" -m tests.production_tasks_ui_server --port ${run.port}`, cwd: repo,
    env: { ...run.env,
      CREATOROS_E2E_DIST: process.env.CREATOROS_E2E_DIST ?? path.join(repo, "tmp", "compact-results-20261030", "dist") },
    url: `${run.url}/api/health`, reuseExistingServer: false, timeout: 120_000,
  },
});
