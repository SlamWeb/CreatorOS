import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "..");
const e2eRoot = mkdtempSync(path.join(tmpdir(), "creatoros-studio-e2e-"));
const python = process.env.CREATOROS_PYTHON ?? "python";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  workers: 1,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:8877",
    channel: "chrome",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `"${python}" -m tests.studio_e2e_server --port 8877`,
    cwd: repoRoot,
    env: { ...process.env, CREATOROS_E2E_ROOT: e2eRoot },
    url: "http://127.0.0.1:8877/api/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
