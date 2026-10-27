import { defineConfig } from "@playwright/test";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
export default defineConfig({
  testDir: "./e2e", testMatch: "agent-plan.spec.ts", timeout: 60_000, workers: 1, reporter: "line",
  outputDir: "../tmp/agent-plan-ui-results",
  use: { baseURL: "http://127.0.0.1:8791", channel: "chrome", viewport: { width: 1440, height: 900 }, screenshot: "only-on-failure" },
  webServer: {
    command: `"${process.env.CREATOROS_PYTHON ?? "python"}" -m tests.production_tasks_ui_server --port 8791`, cwd: repo,
    env: { ...process.env, CREATOROS_E2E_ROOT: path.join(repo, "tmp", `agent-plan-${Date.now()}`), CREATOROS_E2E_DIST: path.join(repo, "tmp", "agent-plan-dist") },
    url: "http://127.0.0.1:8791/api/health", reuseExistingServer: false, timeout: 120_000,
  },
});
