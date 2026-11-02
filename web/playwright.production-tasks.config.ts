import { defineConfig } from "@playwright/test";
import path from "node:path";
import { isolatedBrowserRun } from "./playwright.isolated";

const repo = path.resolve(import.meta.dirname, "..");
const run = isolatedBrowserRun("production-tasks", 8789);
const dist = process.env.PRODUCTION_TASKS_DIST ?? path.join(repo, "tmp", "production-tasks-20261027", "dist");
const python = process.env.CREATOROS_PYTHON ?? "python";

// Real isolated HTTP/SQLite, deterministic local E2EProducer; this suite never calls paid Codex.
export default defineConfig({
  testDir: "./e2e", testMatch: "production-tasks.spec.ts", timeout: 90_000, workers: 1, reporter: "line",
  outputDir: run.output,
  use: { baseURL: run.url, channel: "chrome", viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: {
    command: `"${python}" -m tests.production_tasks_ui_server --port ${run.port}`,
    cwd: repo, env: { ...run.env, CREATOROS_E2E_DIST: dist },
    url: `${run.url}/api/health`, reuseExistingServer: false, timeout: 120_000,
  },
});
