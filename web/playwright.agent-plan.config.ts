import { defineConfig } from "@playwright/test";
import path from "node:path";
import { isolatedBrowserRun } from "./playwright.isolated";

const repo = path.resolve(import.meta.dirname, "..");
const run = isolatedBrowserRun("agent-plan", 8791);
export default defineConfig({
  testDir: "./e2e", testMatch: "agent-plan.spec.ts", timeout: 60_000, workers: 1, reporter: "line",
  outputDir: run.output,
  use: { baseURL: run.url, channel: "chrome", viewport: { width: 1440, height: 900 }, screenshot: "only-on-failure" },
  webServer: {
    command: `"${process.env.CREATOROS_PYTHON ?? "python"}" -m tests.production_tasks_ui_server --port ${run.port}`, cwd: repo,
    env: { ...run.env, CREATOROS_UI_HOLD_PLAN_CONTINUATION: "1", CREATOROS_E2E_DIST: process.env.CREATOROS_E2E_DIST ?? path.join(repo, "tmp", "agent-plan-dist") },
    url: `${run.url}/api/health`, reuseExistingServer: false, timeout: 120_000,
  },
});
