import { defineConfig } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { readBatchPlan } from "./skill-edit-batch-safety.mjs";
import { controlledEnvironment } from "./e2e-safety.mjs";

if (process.env.CREATOROS_SKILL_EDIT_LIVE !== "1") throw new Error("CREATOROS_SKILL_EDIT_LIVE=1 is required for this paid test");
const owner = process.env.CREATOROS_SKILL_EDIT_INTERNAL_OWNER;
const plan = readBatchPlan(process.env.CREATOROS_SKILL_EDIT_ROOT, { owned: !!owner });
const identity = path.join(plan.workspace, "browser-owner.json");
if (owner) {
  const saved = JSON.parse(readFileSync(identity, "utf8"));
  if (saved.owner !== owner || JSON.stringify(saved.plan) !== JSON.stringify(plan)) throw new Error("Skill edit config ownership changed");
} else {
  const token = randomUUID();
  writeFileSync(identity, JSON.stringify({ owner: token, plan }), { flag: "wx" });
  process.env.CREATOROS_SKILL_EDIT_INTERNAL_OWNER = token;
}
const env = controlledEnvironment(process.env, plan.server, plan.dist);
Object.assign(env, { DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY ?? "", CREATOROS_SKILL_EDIT_LIVE: "1" });
// No secrets in Playwright JSON configuration: the child inherits the current
// sanitized environment. Explicitly enumerate only safe overrides here.
for (const key of Object.keys(process.env)) {
  if (!(key in env) && /(TOKEN|SECRET|PASSWORD|PASSWD|COOKIE|CREDENTIAL|API_KEY|PRIVATE_KEY)/i.test(key)) delete process.env[key];
}
Object.assign(process.env, env, { CREATOROS_SKILL_EDIT_ROOT: plan.workspace,
  CREATOROS_SKILL_EDIT_INTERNAL_OWNER: process.env.CREATOROS_SKILL_EDIT_INTERNAL_OWNER,
  TEMP: path.join(plan.workspace, "temp"), TMP: path.join(plan.workspace, "temp"), TMPDIR: path.join(plan.workspace, "temp") });
export default defineConfig({
  testDir: "./media-agent-tests", testMatch: "skill-edit-batch.spec.ts", workers: 1, retries: 0, repeatEach: 1,
  timeout: 300_000, outputDir: plan.output, reporter: "line",
  use: { baseURL: plan.url, channel: "chrome", viewport: { width: 1440, height: 900 },
    actionTimeout: 15_000, navigationTimeout: 30_000, trace: "on", screenshot: "only-on-failure" },
  webServer: {
    command: `"${plan.python}" -m tests.skill_edit_batch_live --serve --live-models --root "${plan.server}" --dist "${plan.dist}" --port ${new URL(plan.url).port} --fixture-id ${plan.fixture_id}`,
    cwd: path.resolve(import.meta.dirname, ".."), env: { PYTHON_DOTENV_DISABLED: "1" },
    url: `${plan.url}/api/skill-assets-probe/evidence`, reuseExistingServer: false, timeout: 120_000,
  },
});
