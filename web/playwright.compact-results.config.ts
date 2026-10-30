import { defineConfig } from "@playwright/test";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
const root = path.join(repo, "tmp", `compact-results-data-${Date.now()}`);
const python = process.env.CREATOROS_PYTHON ?? "python";

// Isolated HTTP/SQLite, controlled chat and local producer. No paid model calls.
export default defineConfig({
  testDir: "./e2e", testMatch: ["compact-chat.spec.ts", "single-task-preview.spec.ts", "agent-transcript.spec.ts"],
  timeout: 90_000, workers: 1, reporter: "line",
  outputDir: "../tmp/compact-results-20261030/browser",
  use: { baseURL: "http://127.0.0.1:8789", channel: "chrome", viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: {
    command: `"${python}" -m tests.production_tasks_ui_server --port 8789`, cwd: repo,
    env: { ...process.env, CREATOROS_E2E_ROOT: root,
      CREATOROS_E2E_DIST: path.join(repo, "tmp", "compact-results-20261030", "dist") },
    url: "http://127.0.0.1:8789/api/health", reuseExistingServer: false, timeout: 120_000,
  },
});
