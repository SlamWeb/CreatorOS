import { defineConfig } from "@playwright/test";
import path from "node:path";

// Explicit paid opt-in. Ordinary e2e never discovers these tests.
const python = process.env.CREATOROS_PYTHON ?? "python";
const repoRoot = path.resolve(import.meta.dirname, "..");
const cases = Array.from({ length: 12 }, (_, index) => `E${String(index + 1).padStart(2, "0")}`);
const batchId = process.env.CREATOROS_EVAL_BATCH_ID;
const phase = process.env.CREATOROS_EVAL_PHASE;
const variant = process.env.CREATOROS_EVAL_E09_VARIANT ?? "failed";
if (batchId && !/^[A-Za-z0-9_-]{1,80}$/.test(batchId)) throw new Error("Invalid evaluation batch id");
if (phase && !["baseline", "regression"].includes(phase)) throw new Error("Invalid evaluation phase");
if (!["failed", "unknown"].includes(variant)) throw new Error("Invalid E09 variant");
const selected = process.argv.flatMap((arg, index, args) => arg === "--project" ? [args[index + 1]] : arg.startsWith("--project=") ? [arg.slice(10)] : []);
export default defineConfig({
  testDir: "./live-eval", timeout: 2_100_000, workers: 1, retries: 0, reporter: "line",
  // Ordinary UI regression clears test-results/. Keep paid-run artifacts apart.
  outputDir: batchId ? `./tmp/live-eval-artifacts/${batchId}/${phase}/${variant}` : "./tmp/live-eval-artifacts",
  use: { channel: "chrome", viewport: { width: 1440, height: 900 },
    permissions: ["clipboard-read", "clipboard-write"], trace: "on", screenshot: "only-on-failure" },
  projects: cases.map((name, index) => ({ name, use: { baseURL: `http://127.0.0.1:${8878 + index}` } })),
  webServer: cases.map((name, index) => ({ name, index })).filter(({ name }) => !selected.length || selected.includes(name)).map(({ name, index }) => ({
    command: `"${python}" -m creatoros.evaluation.browser --case ${name} --port ${8878 + index}`
      + (batchId ? ` --batch-id ${batchId}` : "") + (phase ? ` --phase ${phase}` : "")
      + (name === "E09" ? ` --variant ${variant}` : ""),
    cwd: repoRoot, url: `http://127.0.0.1:${8878 + index}/api/health`,
    // app.py has a module-level default app: isolate even its unused database.
    env: { DATABASE_URL: `sqlite:///${repoRoot.replaceAll("\\", "/")}/data/agent-eval/bootstrap-${name}.db` },
    reuseExistingServer: false, timeout: 90_000,
  })),
});
