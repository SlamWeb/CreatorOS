import { defineConfig } from "@playwright/test";
import path from "node:path";

// Separate opt-in paid suite: no mock model, SDK, producer, or intercepted API.
const python = process.env.CREATOROS_PYTHON ?? "python";
const root = path.resolve(import.meta.dirname, "..");
const cases = ["A14", "S13", "P01", "P02"];
const batch = process.env.CREATOROS_WORKBENCH_BATCH;
const revision = process.env.CREATOROS_WORKBENCH_REVISION;
const listing = process.argv.includes("--list");
if (!listing && (!batch || !revision)) throw new Error("先 review/commit/freeze，再设置 CREATOROS_WORKBENCH_BATCH 与 CREATOROS_WORKBENCH_REVISION。");
for (const value of [batch, revision]) if (value && !/^[A-Za-z0-9_-]{1,80}$/.test(value)) throw new Error("非法工作台批次/冻结名称。");
const selected = process.argv.flatMap((arg, index, args) => arg === "--project" ? [args[index + 1]] : arg.startsWith("--project=") ? [arg.slice(10)] : []);
export default defineConfig({
  testDir: "./live-workbench", timeout: 2_100_000, workers: 1, retries: 0, reporter: "line",
  outputDir: `./tmp/live-workbench/${batch ?? "not-started"}`,
  use: { channel: "chrome", viewport: { width: 1440, height: 900 },
    permissions: ["clipboard-read", "clipboard-write"], trace: "on", screenshot: "only-on-failure" },
  projects: cases.map((name, index) => ({ name, use: { baseURL: `http://127.0.0.1:${8890 + index}` } })),
  webServer: cases.map((name, index) => ({ name, index })).filter(({ name }) => !selected.length || selected.includes(name)).map(({ name, index }) => ({
    command: `"${python}" -m creatoros.evaluation.workbench serve --case ${name} --port ${8890 + index} --batch-id ${batch} --revision ${revision}`,
    cwd: root, url: `http://127.0.0.1:${8890 + index}/api/health`,
    env: { DATABASE_URL: `sqlite:///${root.replaceAll("\\", "/")}/data/agent-eval-workbench/bootstrap-${name}.db` },
    reuseExistingServer: false, timeout: 90_000,
  })),
});
