import { defineConfig } from "@playwright/test";
import path from "node:path";
import { isolatedBrowserRun } from "./playwright.isolated";

const root = path.resolve(import.meta.dirname, "..");
const run = isolatedBrowserRun("reference-selection", 8918);
export default defineConfig({
  testDir: "./reference-selection-tests", workers: 1, timeout: 60_000,
  reporter: "line", outputDir: run.output,
  use: { baseURL: run.url, channel: "chrome", viewport: { width: 1440, height: 900 }, screenshot: "only-on-failure", trace: "retain-on-failure" },
  webServer: { command: `"${process.env.CREATOROS_PYTHON ?? "python"}" -m tests.reference_selection_ui_server --port ${run.port}`, cwd: root,
    env: { ...run.env, CREATOROS_E2E_DIST: process.env.REFERENCE_SELECTION_DIST ?? process.env.CREATOROS_E2E_DIST ?? path.join(root, "tmp/reference-selection/dist") },
    url: `${run.url}/api/health`, reuseExistingServer: false, timeout: 120_000 },
});
