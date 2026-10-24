import { defineConfig } from "@playwright/test";

if (!process.env.CREATOROS_MEDIA_AGENT_ROOT) throw new Error("先显式准备隔离素材集和真实DeepSeek服务，再执行付费检查。");
export default defineConfig({
  testDir: "./media-agent-tests", timeout: 240_000, workers: 1, retries: 0, reporter: "line",
  outputDir: process.env.MEDIA_AGENT_OUTPUT ?? "../tmp/media-agent-20261024/browser-first",
  use: { baseURL: process.env.MEDIA_AGENT_BASE_URL ?? "http://127.0.0.1:8893", channel: "chrome", viewport: { width: 1440, height: 900 },
    trace: "on", screenshot: "only-on-failure" },
});
