import { defineConfig } from "@playwright/test";
import path from "node:path";
const root = path.resolve(import.meta.dirname, "..");
const python = process.env.CREATOROS_PYTHON ?? "python";
const existingRun = process.env.CREATOROS_WORKBENCH_READ_RUN_ID;
const port = Number(process.env.CREATOROS_WORKBENCH_READ_PORT ?? "8894");
const scenario = process.env.CREATOROS_WORKBENCH_READ_CASE ?? "S13";
if (!["S13", "P05"].includes(scenario) || !Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("非法免费预检配置");
export default defineConfig({
  testDir: "./live-workbench-reader", timeout: 90_000, workers: 1, retries: 0, reporter: "line",
  outputDir: "./tmp/workbench-reader-smoke", use: { channel: "chrome", baseURL: `http://127.0.0.1:${port}`, trace: "on" },
  webServer: { command: existingRun
      ? `"${python}" -m creatoros.evaluation.workbench view --run-id ${existingRun} --port ${port}`
      : `"${python}" -m tests.workbench_read_server --port ${port} --case ${scenario}`, cwd: root,
    url: `http://127.0.0.1:${port}/api/health`, reuseExistingServer: false, timeout: 90_000,
    env: { DATABASE_URL: `sqlite:///${root.replaceAll("\\", "/")}/data/agent-eval-workbench/bootstrap-read-smoke.db` } },
});
