import { defineConfig } from "@playwright/test";
import { existsSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { isolatedBrowserRun } from "./playwright.isolated";

const repo = path.resolve(import.meta.dirname, "..");
const boundary = realpathSync(path.join(repo, "tmp"));
if (boundary !== path.join(realpathSync(repo), "tmp")) throw new Error("Checkout tmp must not redirect elsewhere");
const inside = (root: string, value: string) => {
  const relative = path.relative(root, value);
  return !!relative && relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
};
function required(name: string) {
  const value = process.env[name];
  if (!value || !path.isAbsolute(value)) throw new Error(`${name} must be an explicit absolute path`);
  return value;
}
function existing(name: string, kind: "file" | "directory", withinTmp: boolean) {
  const value = realpathSync(required(name));
  const stats = statSync(value);
  if ((kind === "file" ? !stats.isFile() : !stats.isDirectory()) || (withinTmp && !inside(boundary, value))) {
    throw new Error(`${name} must be an isolated ${kind}${withinTmp ? " under this checkout's tmp" : ""}`);
  }
  return value;
}
function fresh(name: string, ownedReload: boolean) {
  const value = path.resolve(required(name));
  const parent = realpathSync(path.dirname(value));
  const resolved = path.join(parent, path.basename(value));
  if (!inside(boundary, resolved) || (existsSync(resolved) && !ownedReload)) {
    throw new Error(`${name} must be a new directory under this checkout's tmp`);
  }
  if (existsSync(resolved) && (!statSync(resolved).isDirectory() || realpathSync(resolved) !== resolved)) {
    throw new Error(`${name} no longer identifies the owned directory`);
  }
  return resolved;
}
// Paths go through Playwright's shell launcher. Do not allow shell expansion.
const quoted = (value: string) => {
  if (/["$`%!\r\n]/.test(value)) throw new Error("Unsupported shell character in media test path");
  return `"${value}"`;
};

const source = existing("MEDIA_SOURCE_DB", "file", true);
const outputs = existing("MEDIA_OUTPUT_ROOT", "directory", true);
const dist = existing("MEDIA_DIST", "directory", false);
if (inside(path.join(repo, "web"), dist) || dist === path.join(repo, "web") || !existsSync(path.join(dist, "index.html"))) {
  throw new Error("MEDIA_DIST must be an independent build containing index.html");
}
// Playwright reloads the config inside worker processes after its own server
// and output directories exist. Only the same invocation may reuse them.
const owner = process.env.MEDIA_INTERNAL_CONFIG_ROOT;
if (process.env.CREATOROS_INTERNAL_E2E_ROOT && !owner) throw new Error("Unexpected inherited E2E run root");
if (owner && owner !== process.env.CREATOROS_INTERNAL_E2E_ROOT) throw new Error("Invalid media config ownership");
const previous = owner ? readFileSync(path.join(owner, "media-config.json"), "utf8") : undefined;
const root = fresh("MEDIA_TEST_ROOT", !!previous);
const result = fresh("MEDIA_TEST_OUTPUT", !!previous);
if (process.platform === "win32" ? root.toLowerCase() === result.toLowerCase() : root === result) {
  throw new Error("MEDIA_TEST_ROOT and MEDIA_TEST_OUTPUT must differ");
}
if (!process.env.CREATOROS_E2E_PORT) throw new Error("CREATOROS_E2E_PORT must be an explicit non-production port");
const run = isolatedBrowserRun("media-collections", 8894);
const creator = process.env.MEDIA_CREATOR_ID ?? "review-lab";
if (!/^[A-Za-z0-9_-]{1,100}$/.test(creator)) throw new Error("MEDIA_CREATOR_ID is invalid");
const python = process.env.CREATOROS_PYTHON ?? "python";
const configuration = JSON.stringify({ source, outputs, dist, root, result, port: run.port, creator, python });
if (previous && previous !== configuration) throw new Error("Media config changed within one invocation");
if (!previous) {
  writeFileSync(path.join(run.root, "media-config.json"), configuration, { flag: "wx" });
  process.env.MEDIA_INTERNAL_CONFIG_ROOT = run.root;
}

export default defineConfig({
  testDir: "./media-tests", timeout: 180_000, workers: 1, reporter: "line", outputDir: result,
  use: { baseURL: run.url, channel: "chrome", viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: {
    command: `${quoted(python)} -m tests.media_collections_live_server --root ${quoted(root)} --dist ${quoted(dist)} --source-db ${quoted(source)} --output-root ${quoted(outputs)} --creator ${creator} --port ${run.port}`,
    cwd: repo, env: run.env, url: `${run.url}/test/media-fixture`, reuseExistingServer: false, timeout: 120_000,
  },
});
