import { defineConfig } from "@playwright/test";
import path from "node:path";
const root = path.resolve(import.meta.dirname, "..");
const python = process.env.CREATOROS_PYTHON ?? "python";
export default defineConfig({
  testDir: "./live-workbench-reader", timeout: 90_000, workers: 1, retries: 0, reporter: "line",
  outputDir: "./tmp/workbench-reader-smoke", use: { channel: "chrome", baseURL: "http://127.0.0.1:8894", trace: "on" },
  webServer: { command: `"${python}" -m tests.workbench_read_server --port 8894`, cwd: root,
    url: "http://127.0.0.1:8894/api/health", reuseExistingServer: false, timeout: 90_000,
    env: { DATABASE_URL: `sqlite:///${root.replaceAll("\\", "/")}/data/agent-eval-workbench/bootstrap-read-smoke.db` } },
});
