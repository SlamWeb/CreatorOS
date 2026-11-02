import { defineConfig } from "@playwright/test";
import { mkdtempSync, readFileSync, realpathSync, statSync } from "node:fs";
import path from "node:path";

// Explicit live-only config. It never starts, stops, or reuses a server.
const configured = process.env.CREATOROS_LIVE_REFERENCE_ROOT;
if (!configured || !path.isAbsolute(configured)) throw new Error("CREATOROS_LIVE_REFERENCE_ROOT must name an explicit isolated root");
const root = realpathSync(configured);
const allowed = realpathSync(path.resolve(import.meta.dirname, "../tmp"));
const relative = path.relative(allowed, root);
if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
  throw new Error("Live reference root must be a dedicated directory under this checkout's tmp");
}
const setup = JSON.parse(readFileSync(path.join(root, "setup.json"), "utf8"));
const target = new URL(setup.url);
if (target.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(target.hostname)
    || !target.port || target.port === "8765" || target.username || target.password
    || target.pathname !== "/" || target.search || target.hash) {
  throw new Error("Live reference URL must be a dedicated local HTTP origin with an explicit port other than 8765");
}
if (typeof setup.series_id !== "string" || !setup.series_id || !Array.isArray(setup.references)
    || setup.references.length !== 2 || setup.references.some((item: unknown) => {
      if (typeof item !== "string" || !path.isAbsolute(item)) return true;
      try {
        const file = realpathSync(item);
        const rel = path.relative(root, file);
        return !rel || rel === ".." || rel.startsWith(`..${path.sep}`) || path.isAbsolute(rel) || !statSync(file).isFile();
      } catch { return true; }
    })) throw new Error("Live setup requires a series ID and two local references inside the isolated root");
const outputDir = mkdtempSync(path.join(root, "browser-results-"));
console.log(`Live reference evidence: ${outputDir}; target: ${target.origin}`);

export default defineConfig({
  testDir: "./e2e", testMatch: "live-topic-references.spec.ts", timeout: 1_200_000,
  workers: 1, retries: 0, reporter: "line", outputDir,
  use: { baseURL: target.origin, channel: "chrome", viewport: { width: 1440, height: 900 },
    screenshot: "only-on-failure", trace: "retain-on-failure" },
});
