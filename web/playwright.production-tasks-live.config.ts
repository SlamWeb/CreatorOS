import { defineConfig } from "@playwright/test";
import { existsSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temporary = realpathSync(path.join(repository, "tmp"));
if (temporary !== path.join(realpathSync(repository), "tmp")) throw new Error("Repository tmp must not redirect elsewhere");
function isolated(name: string, existing: boolean | undefined) {
  const value = process.env[name];
  if (!value || !path.isAbsolute(value)) throw new Error(`${name}: explicit absolute path required`);
  const target = path.resolve(value);
  let ancestor = target;
  while (!existsSync(ancestor)) ancestor = path.dirname(ancestor);
  const physical = path.resolve(realpathSync(ancestor), path.relative(ancestor, target));
  const relative = path.relative(temporary, physical);
  if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)
      || (existing !== undefined && existsSync(target) !== existing)
      || (existsSync(target) && !statSync(target).isDirectory()))
    throw new Error(`${name}: use ${existing ? "an existing" : "a fresh"} isolated directory below repository tmp`);
  return physical;
}
const mode = process.env.CREATOROS_TASKS_LIVE_MODELS;
if (mode !== "0" && mode !== "1") throw new Error("CREATOROS_TASKS_LIVE_MODELS must explicitly be 0 (preparation) or 1 (paid production)");
if (process.env.CREATOROS_TASKS_EXISTING_SESSION) throw new Error("Historical session continuation is not supported by this fresh-only entrypoint");
const root = isolated("CREATOROS_TASKS_LIVE_ROOT", true);
const owner = process.env.CREATOROS_TASKS_INTERNAL_CONFIG_OWNER;
const output = isolated("CREATOROS_TASKS_LIVE_OUTPUT", owner ? undefined : false);
if (!path.relative(output, root).startsWith("..") || !path.relative(root, output).startsWith(".."))
  throw new Error("CREATOROS_TASKS_LIVE_OUTPUT must be separate from the server root");
const setup = JSON.parse(readFileSync(path.join(root, "setup.json"), "utf8"));
const baseURL = process.env.CREATOROS_TASKS_LIVE_URL;
if (!baseURL) throw new Error("CREATOROS_TASKS_LIVE_URL: explicit isolated URL required");
const target = new URL(baseURL);
if (target.protocol !== "http:" || target.hostname !== "127.0.0.1" || !target.port || target.port === "8765"
    || target.username || target.password || target.pathname !== "/" || target.search || target.hash
    || baseURL.replace(/\/$/, "") !== setup.base_url)
  throw new Error("CREATOROS_TASKS_LIVE_URL must match the fixture's non-production loopback URL");
if (setup.mode !== (mode === "1" ? "live-models" : "prepare-only") || path.resolve(setup.root) !== root || !setup.fixture_id)
  throw new Error("CREATOROS_TASKS_LIVE_ROOT must identify a fixture matching the explicit model mode");
const fixtureTemp = realpathSync(setup.temp);
if (!fixtureTemp.startsWith(root + path.sep)) throw new Error("Fixture temp must be within its root");
Object.assign(process.env, { TEMP: fixtureTemp, TMP: fixtureTemp, TMPDIR: fixtureTemp });
// Playwright reloads configuration in its worker after creating outputDir.
// Only children of this invocation may reuse that path; another run gets a new one.
const ownershipFile = output + ".owner.json";
const configuration = { root, output, baseURL, fixture: setup.fixture_id, mode };
if (owner) {
  const saved = JSON.parse(readFileSync(ownershipFile, "utf8"));
  if (saved.owner !== owner || JSON.stringify(saved.configuration) !== JSON.stringify(configuration))
    throw new Error("Production task config ownership changed");
} else {
  const token = randomUUID();
  writeFileSync(ownershipFile, JSON.stringify({ owner: token, configuration }), { flag: "wx" });
  process.env.CREATOROS_TASKS_INTERNAL_CONFIG_OWNER = token;
}
export default defineConfig({
  testDir: "./production-tasks-live", testMatch: mode === "1" ? "tasks.spec.ts" : "fixture-http.spec.ts",
  timeout: 1_500_000, workers: 1, retries: 0,
  outputDir: output, reporter: "line",
  use: { baseURL, channel: "chrome", viewport: { width: 1440, height: 900 },
    actionTimeout: 15000, navigationTimeout: 30000, trace: "on", screenshot: "only-on-failure" },
});
