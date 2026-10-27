import { defineConfig } from "@playwright/test";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
const root = path.join(repo, "tmp", `production-tasks-ui-${Date.now()}`);
const dist = process.env.PRODUCTION_TASKS_DIST ?? path.join(repo, "tmp", "production-tasks-20261027", "dist");
const python = process.env.CREATOROS_PYTHON ?? "python";

// Real isolated HTTP/SQLite, deterministic local E2EProducer; this suite never calls paid Codex.
export default defineConfig({
  testDir: "./e2e", testMatch: "production-tasks.spec.ts", timeout: 90_000, workers: 1, reporter: "line",
  outputDir: process.env.PRODUCTION_TASKS_TEST_OUTPUT ?? "../tmp/production-tasks-20261027/browser-first",
  use: { baseURL: "http://127.0.0.1:8789", channel: "chrome", viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: {
    command: `"${python}" -m tests.production_tasks_ui_server --port 8789`,
    cwd: repo, env: { ...process.env, CREATOROS_E2E_ROOT: root, CREATOROS_E2E_DIST: dist },
    url: "http://127.0.0.1:8789/api/health", reuseExistingServer: false, timeout: 120_000,
  },
});
