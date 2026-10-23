import { defineConfig } from "@playwright/test";

// Explicit opt-in only: this talks to a separately prepared real DeepSeek host.
// Ordinary Playwright/UI/CI suites never discover this directory.
if (!process.env.CREATOROS_CONTEXT_FOCUS_ROOT) throw new Error("先准备独立地球 online 副本，再显式运行此付费检查。");
export default defineConfig({
  testDir: "./live-context-focus", timeout: 240_000, workers: 1, retries: 0, reporter: "line",
  outputDir: `../tmp/context-focus-live-${new Date().toISOString().replace(/[:.]/g, "-")}`,
  use: { baseURL: "http://127.0.0.1:8896", channel: "chrome", viewport: { width: 1440, height: 900 },
    trace: "on", screenshot: "only-on-failure" },
});
