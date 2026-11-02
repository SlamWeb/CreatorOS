import { mkdirSync, mkdtempSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const env = { ...process.env };
const requested = env.CREATOROS_BUILD_OUT_DIR;
if (requested !== undefined) {
  if (!requested || !path.isAbsolute(requested)) throw new Error("CREATOROS_BUILD_OUT_DIR must be a new absolute directory");
  // Never empty an existing tree: this also rejects a second build using the
  // same output. The user can inspect a failed partial build before retrying.
  mkdirSync(requested);
  env.CREATOROS_BUILD_OUT_DIR = realpathSync(requested);
}
const temporary = mkdtempSync(path.join(tmpdir(), "creatoros-vite-build-"));
Object.assign(env, { CREATOROS_VITE_CACHE_DIR: path.join(temporary, "vite-cache"),
  TEMP: temporary, TMP: temporary, TMPDIR: temporary });
console.log(`Vite build output: ${env.CREATOROS_BUILD_OUT_DIR ?? path.join(import.meta.dirname, "dist")}`);
const result = spawnSync(process.execPath, [path.join(import.meta.dirname, "node_modules/vite/bin/vite.js"),
  "build", "--configLoader", "runner"], { cwd: import.meta.dirname, env, stdio: "inherit" });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
