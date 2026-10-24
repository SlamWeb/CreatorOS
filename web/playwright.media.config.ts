import { defineConfig } from "@playwright/test";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
const root = path.join(repo, "tmp", `media-collections-browser-${Date.now()}`);
const dist = path.join(repo, "tmp", "media-collections-20261024", "dist");
const python = process.env.CREATOROS_PYTHON ?? "python";

export default defineConfig({
  testDir: "./media-tests", timeout: 180_000, workers: 1, reporter: "line",
  outputDir: process.env.MEDIA_TEST_OUTPUT ?? "../tmp/media-collections-20261024/browser-first",
  use: { baseURL: "http://127.0.0.1:8894", channel: "chrome", viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: {
    command: `"${python}" -m tests.media_collections_live_server --root "${root}" --dist "${dist}" --port 8894`,
    cwd: repo, url: "http://127.0.0.1:8894/test/media-fixture", reuseExistingServer: false, timeout: 120_000,
  },
});
