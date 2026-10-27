import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const data = mkdtempSync(path.join(tmpdir(), "creatoros-staged-ui-"));
export default defineConfig({
  testDir: "./e2e", testMatch: ["staged-production.spec.ts", "production-tasks.spec.ts"], workers: 1,
  timeout: 60_000, reporter: "line", outputDir: "../tmp/staged-ui/test-results",
  use: { baseURL: "http://127.0.0.1:8877", channel: "chrome", viewport: { width: 1440, height: 900 }, screenshot: "only-on-failure", trace: "retain-on-failure" },
  webServer: { command: `"${process.env.CREATOROS_PYTHON ?? "python"}" -m tests.production_tasks_ui_server --port 8877`, cwd: root,
    env: { ...process.env, CREATOROS_E2E_ROOT: data, CREATOROS_E2E_DIST: path.join(root, "tmp/staged-ui/dist") },
    url: "http://127.0.0.1:8877/api/health", reuseExistingServer: false, timeout: 120_000 },
});
