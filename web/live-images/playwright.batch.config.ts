import { defineConfig } from "@playwright/test";
import { existsSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import path from "node:path";

const repo = realpathSync(path.resolve(import.meta.dirname, "../.."));
const boundary = realpathSync(path.join(repo, "tmp"));
if (boundary !== path.join(repo, "tmp")) throw new Error("Repository tmp must not redirect elsewhere");
const within = (parent: string, target: string) => {
  const relative = path.relative(parent, target);
  return !!relative && relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
};
function isolated(name: string, fresh: boolean) {
  const value = process.env[name];
  if (!value || !path.isAbsolute(value)) throw new Error(`${name}: explicit absolute path required`);
  const target = path.resolve(value);
  // Require an existing parent so no path is implicitly borrowed or created.
  const physical = existsSync(target) ? realpathSync(target) : path.join(realpathSync(path.dirname(target)), path.basename(target));
  if (!within(boundary, physical) || (fresh && existsSync(target))
      || (existsSync(target) && !statSync(target).isDirectory())) throw new Error(`${name}: isolated ${fresh ? "fresh " : ""}directory required`);
  return physical;
}
const mode = process.env.CREATOROS_BATCH_IMAGES_LIVE;
if (mode !== "0" && mode !== "1") throw new Error("CREATOROS_BATCH_IMAGES_LIVE must explicitly be 0 (local preparation) or 1 (real Codex)");
const root = isolated("CREATOROS_BATCH_IMAGES_ROOT", false);
const setup = JSON.parse(readFileSync(path.join(root, "batch-setup.json"), "utf8"));
const suppliedURL = process.env.CREATOROS_BATCH_IMAGES_URL;
if (!suppliedURL || suppliedURL !== setup.url) throw new Error("CREATOROS_BATCH_IMAGES_URL must exactly match this fixture");
const url = new URL(suppliedURL);
if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port || url.port === "8765"
    || url.username || url.password || url.pathname !== "/" || url.hash || url.search) throw new Error("Use a non-production loopback fixture URL");
if (setup.root !== root || setup.kind !== "batch-images-15-v1" || !setup.fixture_id
    || setup.live_models !== (mode === "1")) throw new Error("Fixture identity or live authorization does not match");
for (const name of ["database", "skills_root", "temp", "codex_home"]) {
  const target = setup[name];
  if (typeof target !== "string" || !path.isAbsolute(target) || !within(root, path.resolve(target))) throw new Error(`Fixture ${name} must be isolated`);
}
const dist = realpathSync(setup.dist);
if (!within(boundary, dist) || within(root, dist) || within(dist, root) || dist === root
    || !existsSync(path.join(dist, "index.html"))) throw new Error("Fixture build must be independent");
const temp = realpathSync(setup.temp);
if (!within(root, temp)) throw new Error("Fixture temp redirected outside root");
const owner = process.env.CREATOROS_BATCH_IMAGES_INTERNAL_OWNER;
const output = isolated("CREATOROS_BATCH_IMAGES_OUTPUT", !owner);
if (output === root || within(output, root) || within(root, output) || output === dist || within(output, dist) || within(dist, output))
  throw new Error("Results must be separate from fixture data and build");
const ownership = output + ".owner.json";
const configuration = { root, output, fixture_id: setup.fixture_id, url: suppliedURL, mode };
if (owner) {
  const saved = JSON.parse(readFileSync(ownership, "utf8"));
  if (saved.owner !== owner || JSON.stringify(saved.configuration) !== JSON.stringify(configuration)) throw new Error("Invalid worker configuration ownership");
} else {
  const token = randomUUID();
  writeFileSync(ownership, JSON.stringify({ owner: token, configuration }), { flag: "wx" });
  process.env.CREATOROS_BATCH_IMAGES_INTERNAL_OWNER = token;
}
Object.assign(process.env, { TEMP: temp, TMP: temp, TMPDIR: temp });
export default defineConfig({
  testDir: ".", testMatch: mode === "1" ? "extraction-batch.live.spec.ts" : "batch-preparation.spec.ts",
  timeout: 720_000, workers: 1, retries: 0, outputDir: output, reporter: "line",
  use: { baseURL: suppliedURL, channel: "chrome", viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure", screenshot: "only-on-failure" },
});
