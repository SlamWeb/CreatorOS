import { mkdirSync, mkdtempSync, realpathSync, writeFileSync, readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { createServer } from "node:net";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { controlledEnvironment } from "./e2e-safety.mjs";

const args = process.argv.slice(2);
if (args.length !== 1 || !["--prepare-only", "--live"].includes(args[0])) throw new Error("Use --prepare-only or --live; each invocation creates fresh evidence");
const live = args[0] === "--live";
if (live && (process.env.CREATOROS_SKILL_EDIT_LIVE !== "1" || !process.env.DEEPSEEK_API_KEY?.trim())) {
  throw new Error("--live requires CREATOROS_SKILL_EDIT_LIVE=1 and DEEPSEEK_API_KEY in the environment");
}
const repo = path.resolve(import.meta.dirname, "..");
mkdirSync(path.join(repo, "tmp"), { recursive: true });
const temporary = realpathSync(path.join(repo, "tmp"));
if (temporary !== path.join(realpathSync(repo), "tmp")) throw new Error("Checkout tmp must not redirect elsewhere");
const workspace = mkdtempSync(path.join(temporary, "skill-edit-batch-"));
mkdirSync(path.join(workspace, "temp"));
const dist = path.join(workspace, "dist");
const env = controlledEnvironment(process.env, workspace, dist);
Object.assign(env, { TEMP: path.join(workspace, "temp"), TMP: path.join(workspace, "temp"), TMPDIR: path.join(workspace, "temp") });
const server = createServer();
await new Promise((resolve, reject) => server.once("error", reject).listen(0, "127.0.0.1", resolve));
const port = server.address().port;
await new Promise(resolve => server.close(resolve));
if (port === 8765) throw new Error("Refusing production port");
const plan = { protocol: "skill-edit-batch-v1", workspace, fixture_id: randomUUID().replaceAll("-", ""),
  server: path.join(workspace, "server"), output: path.join(workspace, "browser-results"), dist,
  url: `http://127.0.0.1:${port}`, python: env.CREATOROS_PYTHON ?? (process.platform === "win32" ? "python" : "python3") };
writeFileSync(path.join(workspace, "run.json"), JSON.stringify(plan, null, 2), { flag: "wx" });
const summary = { mode: args[0], status: "preparing", fixture_id: plan.fixture_id, workspace, passed: 0,
  live_model_test: "not_run", model_scope: "DeepSeek account conversation only; no Codex or production" };
const save = () => writeFileSync(path.join(workspace, "summary.json"), JSON.stringify(summary, null, 2));
console.log(`Skill batch evidence: ${workspace}`);
save();
function run(executable, argv, cwd, childEnv = env) {
  const result = spawnSync(executable, argv, { cwd, env: childEnv, stdio: "inherit" });
  if (result.error || result.status !== 0) throw new Error(result.error?.message ?? `${argv[0]} exited ${result.status}`);
}
try {
  run(process.execPath, ["typecheck.mjs"], import.meta.dirname);
  run(process.execPath, ["build.mjs"], import.meta.dirname);
  if (!live) {
    run(plan.python, ["-m", "tests.skill_edit_batch_live", "--prepare-only", "--root", plan.server,
      "--dist", dist, "--port", String(port), "--fixture-id", plan.fixture_id], repo);
    summary.fixture = JSON.parse(readFileSync(path.join(plan.server, "setup.json"), "utf8"));
    summary.status = "prepared_not_run";
  } else {
    Object.assign(env, { CREATOROS_SKILL_EDIT_ROOT: workspace, CREATOROS_SKILL_EDIT_LIVE: "1",
      DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY, PLAYWRIGHT_JSON_OUTPUT_FILE: path.join(workspace, "playwright.json") });
    summary.live_model_test = "started_result_unknown";
    save();
    run(process.execPath, ["node_modules/@playwright/test/cli.js", "test", "--config", "playwright.skill-edit-batch.config.ts", "--reporter=line,json"], import.meta.dirname);
    const report = JSON.parse(readFileSync(env.PLAYWRIGHT_JSON_OUTPUT_FILE, "utf8"));
    summary.stats = report.stats;
    if (report.errors?.length || report.stats.expected !== 1 || report.stats.unexpected || report.stats.skipped || report.stats.flaky) throw new Error("Live report is incomplete or failed");
    summary.passed = 1;
    summary.status = "live_passed";
    summary.live_model_test = "passed";
  }
} catch (error) {
  summary.status = "failed_or_unknown";
  summary.error = error.message;
  process.exitCode = 1;
} finally {
  save();
  console.log(`Summary: ${path.join(workspace, "summary.json")}; never reuse a failed/unknown fixture`);
}
