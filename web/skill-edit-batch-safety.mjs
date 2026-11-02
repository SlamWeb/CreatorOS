import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import path from "node:path";

export function readBatchPlan(workspace, { owned = false } = {}) {
  const repo = path.resolve(import.meta.dirname, "..");
  const boundary = realpathSync(path.join(repo, "tmp"));
  if (boundary !== path.join(realpathSync(repo), "tmp")) throw new Error("Checkout tmp must not redirect elsewhere");
  if (!workspace || !path.isAbsolute(workspace)) throw new Error("CREATOROS_SKILL_EDIT_ROOT must be an explicit absolute path");
  const root = realpathSync(workspace);
  const relative = path.relative(boundary, root);
  if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error("Skill edit workspace must be below this checkout's tmp");
  }
  const plan = JSON.parse(readFileSync(path.join(root, "run.json"), "utf8"));
  if (plan.protocol !== "skill-edit-batch-v1" || !/^[a-f0-9]{32}$/.test(plan.fixture_id)
      || plan.workspace !== root || plan.server !== path.join(root, "server")
      || plan.output !== path.join(root, "browser-results") || plan.dist !== path.join(root, "dist")) {
    throw new Error("Invalid isolated Skill edit plan");
  }
  const url = new URL(plan.url);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port || url.port === "8765"
      || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("Skill edit URL must use an explicit loopback port other than 8765");
  }
  if (!statSync(plan.dist).isDirectory() || realpathSync(plan.dist) !== plan.dist
      || !existsSync(path.join(plan.dist, "index.html"))) throw new Error("Missing independent frontend build");
  for (const name of ["server", "output"]) {
    if (existsSync(plan[name]) && (!owned || realpathSync(plan[name]) !== plan[name] || !statSync(plan[name]).isDirectory())) {
      throw new Error(`Skill edit ${name} must be fresh; inspect existing results instead of rerunning`);
    }
  }
  if (typeof plan.python !== "string" || !plan.python || /["$`%!\r\n]/.test(plan.python + root)) {
    throw new Error("Unsupported shell character in Skill edit paths or Python executable");
  }
  return plan;
}
