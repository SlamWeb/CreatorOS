import { defineConfig } from "@playwright/test";
import path from "node:path";

const outputDir = process.env.CREATOROS_CALIBRATION_OUTPUT ??= path.resolve(
  import.meta.dirname, `../tmp/calibration-browser-${Date.now()}`);

// Paid, explicit opt-in; server is started separately with a new isolated root.
export default defineConfig({
  testDir: "./live-calibration",
  timeout: 1_800_000,
  workers: 1,
  retries: 0,
  outputDir,
  reporter: [["line"], ["json", { outputFile: path.join(outputDir, "report.json") }]],
  use: {
    baseURL: "http://127.0.0.1:8898",
    channel: "chrome",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
