import { defineConfig } from "@playwright/test";
import path from "node:path";
import { isolatedBrowserRun } from "./playwright.isolated";

const repoRoot = path.resolve(import.meta.dirname, "..");
const run = isolatedBrowserRun("studio-e2e", 8877);
const python = process.env.CREATOROS_PYTHON ?? "python";
const vitePort = process.env.CREATOROS_E2E_VITE_PORT === undefined ? undefined : Number(process.env.CREATOROS_E2E_VITE_PORT);
if (vitePort !== undefined && (!Number.isInteger(vitePort) || vitePort < 1 || vitePort > 65535
    || vitePort === 8765 || vitePort === run.port)) throw new Error("CREATOROS_E2E_VITE_PORT must be a distinct non-production port");
const apiServer = {
  command: `"${python}" -m tests.studio_e2e_server --port ${run.port}`,
  cwd: repoRoot, env: run.env, url: `${run.url}/api/health`, reuseExistingServer: false, timeout: 120_000,
};

export default defineConfig({
  testDir: "./e2e",
  // Paid probes need their own explicitly prepared live entrypoint.
  testIgnore: "live-topic-references.spec.ts",
  timeout: 60_000,
  workers: 1,
  reporter: "line",
  outputDir: run.output,
  use: {
    baseURL: vitePort === undefined ? run.url : `http://127.0.0.1:${vitePort}`,
    channel: "chrome",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: vitePort === undefined ? apiServer : [apiServer, {
    command: `node node_modules/vite/bin/vite.js --configLoader runner --host 127.0.0.1 --port ${vitePort}`,
    cwd: path.join(repoRoot, "web"),
    env: { ...run.env, CREATOROS_API_URL: run.url, CREATOROS_VITE_PORT: String(vitePort),
      CREATOROS_VITE_CACHE_DIR: path.join(run.root, "vite-cache"), CREATOROS_PROXY_TEST_ROUTES: "1" },
    url: `http://127.0.0.1:${vitePort}/`, reuseExistingServer: false, timeout: 120_000,
  }],
});
