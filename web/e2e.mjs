import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { controlledEnvironment } from "./e2e-safety.mjs";

// The ordinary e2e command previously rebuilt web/dist, so parallel tests
// could replace one another's frontend or a locally served build.
const root = mkdtempSync(path.join(tmpdir(), "creatoros-e2e-cli-"));
const temporary = path.join(root, "temp");
mkdirSync(temporary);
const build = path.join(root, "dist");
const env = { ...controlledEnvironment(process.env, root, build), TEMP: temporary, TMP: temporary, TMPDIR: temporary };
// Explicit non-production ports remain the caller's choice for this entrypoint.
if (process.env.CREATOROS_E2E_PORT) env.CREATOROS_E2E_PORT = process.env.CREATOROS_E2E_PORT;
if (process.env.CREATOROS_E2E_VITE_PORT) env.CREATOROS_E2E_VITE_PORT = process.env.CREATOROS_E2E_VITE_PORT;
console.log(`E2E build and temporary files: ${root}`);
const commands = [
  [path.join(import.meta.dirname, "typecheck.mjs")],
  [path.join(import.meta.dirname, "build.mjs")],
  [path.join(import.meta.dirname, "node_modules/@playwright/test/cli.js"), "test", ...process.argv.slice(2)],
];
for (const args of commands) {
  const result = spawnSync(process.execPath, args, { cwd: import.meta.dirname, env, stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    process.exitCode = result.status ?? 1;
    break;
  }
}
