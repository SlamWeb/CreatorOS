import { defineConfig } from "@playwright/test";
import path from "node:path";
import { randomUUID } from "node:crypto";
const root = path.resolve(import.meta.dirname, "..");
const python = process.env.CREATOROS_PYTHON ?? "python";
const existingRun = process.env.CREATOROS_WORKBENCH_READ_RUN_ID;
const scenario = process.env.CREATOROS_WORKBENCH_READ_CASE ?? "S13";
const defaultPort = scenario === "P04" ? "8898" : scenario === "P06" ? "8899" : "8894";
const port = Number(process.env.CREATOROS_WORKBENCH_READ_PORT ?? defaultPort);
const artifactKey = `${scenario}-${existingRun ?? "fixture"}-${Date.now()}-${randomUUID().slice(0, 8)}`;
if (!["S13", "P04", "P05", "P06"].includes(scenario) || !Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("非法免费预检配置");
export default defineConfig({
  testDir: "./live-workbench-reader", timeout: 90_000, workers: 1, retries: 0, reporter: "line",
  outputDir: `./tmp/workbench-reader-smoke/${artifactKey}`, use: { channel: "chrome", baseURL: `http://127.0.0.1:${port}`, trace: "on" },
  webServer: { command: existingRun
      ? `"${python}" -m creatoros.evaluation.workbench view --run-id ${existingRun} --port ${port}`
      : `"${python}" -m tests.workbench_read_server --port ${port} --case ${scenario}`, cwd: root,
    url: `http://127.0.0.1:${port}/api/health`, reuseExistingServer: false, timeout: 90_000,
    env: { DATABASE_URL: `sqlite:///${root.replaceAll("\\", "/")}/data/agent-eval-workbench/bootstrap-read-smoke.db` } },
});
