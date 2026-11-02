import { mkdtempSync, rmdirSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

// node_modules can be a junction shared with another checkout. Each invocation
// gets its own TypeScript cache rather than writing through that junction.
const root = mkdtempSync(path.join(tmpdir(), "creatoros-typecheck-"));
const caches = ["app", "node"].map(name => path.join(root, `${name}.tsbuildinfo`));
try {
  for (const [index, name] of ["app", "node"].entries()) {
    const result = spawnSync(process.execPath, [path.join(import.meta.dirname, "node_modules/typescript/bin/tsc"),
      "--project", path.join(import.meta.dirname, `tsconfig.${name}.json`), "--incremental", "true",
      "--tsBuildInfoFile", caches[index], "--pretty", "false"], { cwd: import.meta.dirname, stdio: "inherit" });
    if (result.error) throw result.error;
    if (result.status !== 0) {
      process.exitCode = result.status ?? 1;
      break;
    }
  }
} finally {
  for (const cache of caches) {
    try { unlinkSync(cache); } catch (error) { if (error.code !== "ENOENT") throw error; }
  }
  rmdirSync(root);
}
