import { defineConfig } from "@playwright/test";
import path from "node:path";

// Explicit paid opt-in. Ordinary e2e never discovers these tests.
const python = process.env.CREATOROS_PYTHON ?? "python";
const repoRoot = path.resolve(import.meta.dirname, "..");
const cases = ["E01", "E02"];
const selected = process.argv.flatMap((arg, index, args) => arg === "--project" ? [args[index + 1]] : arg.startsWith("--project=") ? [arg.slice(10)] : []);
export default defineConfig({
  testDir: "./live-eval", timeout: 240_000, workers: 1, retries: 0, reporter: "line",
  // Ordinary UI regression clears test-results/. Keep paid-run artifacts apart.
  outputDir: "./tmp/live-eval-artifacts",
  use: { channel: "chrome", viewport: { width: 1440, height: 900 },
    permissions: ["clipboard-read", "clipboard-write"], trace: "on", screenshot: "only-on-failure" },
  projects: cases.map((name, index) => ({ name, use: { baseURL: `http://127.0.0.1:${8878 + index}` } })),
  webServer: cases.map((name, index) => ({ name, index })).filter(({ name }) => !selected.length || selected.includes(name)).map(({ name, index }) => ({
    command: `"${python}" -m creatoros.evaluation.browser --case ${name} --port ${8878 + index}`,
    cwd: repoRoot, url: `http://127.0.0.1:${8878 + index}/api/health`,
    reuseExistingServer: false, timeout: 90_000,
  })),
});
