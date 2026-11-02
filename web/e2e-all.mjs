import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { createHash } from "node:crypto";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { controlledEnvironment } from "./e2e-safety.mjs";
import { classifyGroup } from "./e2e-results.mjs";

// New specs require a deliberate safety/fixture review before joining this run.
const groups = [
  { name: "general", config: "playwright.config.ts", source: "fresh Studio database with controlled local producer", specs: [
    "account-analytics.spec.ts", "account-chat.spec.ts", "account-navigator.spec.ts",
    "agent-composer-controls.spec.ts", "agent-discussion.spec.ts", "agent-layout.spec.ts",
    "agent-research.spec.ts", "artifact-download.spec.ts", "batch-production.spec.ts",
    "bulk-topic-queue.spec.ts", "chat-image-links.spec.ts", "chat-images.spec.ts", "chat-links.spec.ts",
    "content-navigation.spec.ts", "eval.spec.ts", "fresh-production.spec.ts", "home-page.spec.ts",
    "observation.spec.ts", "polling.spec.ts", "production-progress.spec.ts", "publication-request.spec.ts",
    "reply-trace.spec.ts", "run-workbench.spec.ts", "series-settings.spec.ts", "skill-calibration.spec.ts",
    "skill-composer.spec.ts", "skill-extraction.spec.ts", "skill-inspector.spec.ts", "skill-iterations.spec.ts",
    "studio-preview.spec.ts", "studio-workflow.spec.ts", "task-inbox.spec.ts", "topic-production.spec.ts",
    "topic-references.spec.ts", "topic-removal.spec.ts", "topic-research.spec.ts", "ui-foundation.spec.ts",
    "workbench-locator.spec.ts", "workspace-gallery.spec.ts", "workspace-management.spec.ts",
  ] },
  { name: "agent-plan", config: "playwright.agent-plan.config.ts", source: "fresh plan fixture; continuation held", specs: ["agent-plan.spec.ts"] },
  { name: "staged", config: "playwright.staged.config.ts", source: "fresh task fixture and controlled image/video producer", specs: ["production-tasks.spec.ts", "staged-production.spec.ts"] },
  { name: "compact", config: "playwright.compact-results.config.ts", source: "fresh compact-chat fixture and controlled producer", specs: ["compact-chat.spec.ts", "single-task-preview.spec.ts", "agent-transcript.spec.ts"] },
  { name: "media", config: "playwright.media.config.ts", directory: "media-tests",
    source: "fresh deterministic local image Runs; no model or historical data",
    specs: ["collections.spec.ts", "fixture-http.spec.ts"] },
];
const live = ["live-topic-references.spec.ts"];
const args = process.argv.slice(2);
const planning = args.includes("--plan");
const prepareOnly = args.includes("--prepare-only");
const dirIndex = args.indexOf("--spec-dir");
const mediaDirIndex = args.indexOf("--media-spec-dir");
const groupIndex = args.indexOf("--group");
if (dirIndex !== -1 && (!planning || !args[dirIndex + 1])) throw new Error("--spec-dir is available only with --plan");
if (mediaDirIndex !== -1 && (!planning || !args[mediaDirIndex + 1])) throw new Error("--media-spec-dir is available only with --plan");
if (groupIndex !== -1 && !groups.some(group => group.name === args[groupIndex + 1])) throw new Error("Unknown --group");
if (planning && groupIndex !== -1) throw new Error("--plan always reports the complete inventory");
if (prepareOnly && (planning || groupIndex === -1 || args[groupIndex + 1] !== "media")) {
  throw new Error("--prepare-only requires --group media");
}
const allowed = new Set(["--plan", "--prepare-only", ...(dirIndex === -1 ? [] : ["--spec-dir", args[dirIndex + 1]]),
  ...(mediaDirIndex === -1 ? [] : ["--media-spec-dir", args[mediaDirIndex + 1]]),
  ...(groupIndex === -1 ? [] : ["--group", args[groupIndex + 1]])]);
if (args.some(arg => !allowed.has(arg))) throw new Error("Usage: node e2e-all.mjs [--plan [--spec-dir directory] [--media-spec-dir directory]] [--group name [--prepare-only]]");
const dir = dirIndex === -1 ? path.join(import.meta.dirname, "e2e") : path.resolve(args[dirIndex + 1]);
const mediaDir = mediaDirIndex === -1 ? path.join(import.meta.dirname, "media-tests") : path.resolve(args[mediaDirIndex + 1]);
const inventory = directory => readdirSync(directory, { withFileTypes: true })
  .filter(entry => entry.isFile() && entry.name.endsWith(".spec.ts")).map(entry => entry.name).sort();
const actual = inventory(dir);
const selected = [...groups.filter(group => !group.directory).flatMap(group => group.specs), ...live];
const unknown = actual.filter(name => !selected.includes(name));
const missing = selected.filter(name => !actual.includes(name));
if (new Set(selected).size !== selected.length || unknown.length || missing.length) {
  throw new Error(`E2E spec inventory changed; review unclassified: ${unknown.join(", ") || "none"}; missing: ${missing.join(", ") || "none"}`);
}
const actualMedia = inventory(mediaDir);
const selectedMedia = groups.filter(group => group.directory === "media-tests").flatMap(group => group.specs);
const unknownMedia = actualMedia.filter(name => !selectedMedia.includes(name));
const missingMedia = selectedMedia.filter(name => !actualMedia.includes(name));
if (new Set(selectedMedia).size !== selectedMedia.length || unknownMedia.length || missingMedia.length) {
  throw new Error(`Media spec inventory changed; review unclassified: ${unknownMedia.join(", ") || "none"}; missing: ${missingMedia.join(", ") || "none"}`);
}
const digest = file => createHash("sha256").update(readFileSync(file)).digest("hex");
const specDigests = Object.fromEntries(actual.map(name => [name, digest(path.join(dir, name))]));
const additionalSpecDigests = Object.fromEntries(actualMedia.map(name => [`media-tests/${name}`, digest(path.join(mediaDir, name))]));
const plan = { groups, excluded_live_specs: live, spec_digests: specDigests, additional_spec_digests: additionalSpecDigests };
if (planning) {
  console.log(JSON.stringify(plan, null, 2));
} else {
  const root = mkdtempSync(path.join(tmpdir(), "creatoros-e2e-all-"));
  const temporary = path.join(root, "temp");
  mkdirSync(temporary);
  const dist = path.join(root, "dist");
  const env = { ...controlledEnvironment(process.env, root, dist), TEMP: temporary, TMP: temporary, TMPDIR: temporary };
  const summaryPath = path.join(root, "summary.json");
  const repo = path.resolve(import.meta.dirname, "..");
  const sourceExtensions = new Set([".py", ".ts", ".tsx", ".js", ".mjs", ".css", ".json", ".html", ".md", ".svg"]);
  const frontendAssets = new Set([".png", ".jpg", ".jpeg", ".webp", ".woff", ".woff2"]);
  function sourceFiles() {
    const files = {};
    const visit = directory => {
      if (!existsSync(directory)) return;
      for (const entry of readdirSync(directory, { withFileTypes: true })) {
        if (entry.isSymbolicLink() || ["__pycache__", "node_modules", "tmp", ".git"].includes(entry.name)) continue;
        const file = path.join(directory, entry.name);
        if (entry.isDirectory()) visit(file);
        else if (entry.isFile() && (sourceExtensions.has(path.extname(entry.name))
                 || (file.startsWith(path.join(repo, "web") + path.sep) && frontendAssets.has(path.extname(entry.name))))) {
          const key = path.relative(repo, file).split(path.sep).join("/");
          try { files[key] = digest(file); } catch { files[key] = "unreadable"; }
        }
      }
    };
    for (const folder of ["creatoros", "migrations", "tests", "production-skills", "web/src", "web/public", "web/e2e", "web/media-tests"]) visit(path.join(repo, folder));
    for (const entry of readdirSync(import.meta.dirname, { withFileTypes: true })) {
      if (entry.isFile() && sourceExtensions.has(path.extname(entry.name))) {
        const file = path.join(import.meta.dirname, entry.name);
        files[`web/${entry.name}`] = digest(file);
      }
    }
    return Object.fromEntries(Object.entries(files).sort(([a], [b]) => a.localeCompare(b)));
  }
  const beforeSources = sourceFiles();
  const sourceFingerprint = createHash("sha256").update(JSON.stringify(beforeSources)).digest("hex");
  const summary = {
    scope: groupIndex === -1 ? "all controlled specs" : `group ${args[groupIndex + 1]}`,
    prepare_only: prepareOnly,
    source_fingerprint: sourceFingerprint,
    excluded_live_specs: live,
    excluded_live_tests_declared: live.reduce((total, name) => total +
      [...readFileSync(path.join(dir, name), "utf8").matchAll(/^\s*test\(/gm)].length, 0),
    excluded_live_reason: "Explicitly excluded: real model/production probe requires separate authorization and isolated server.",
    groups: Object.fromEntries(groups.map(group => [group.name, { status: "not_run", directory: group.directory ?? "e2e", specs: group.specs, source: group.source }])),
  };
  const save = () => writeFileSync(summaryPath, JSON.stringify(summary, null, 2));
  const launch = (command, childEnv = env) => spawnSync(process.execPath, command,
    { cwd: import.meta.dirname, env: childEnv, stdio: "inherit" });
  console.log(`E2E artifacts and report: ${root}`);
  save();
  let failed = false;
  for (const script of prepareOnly ? [] : ["typecheck.mjs", "build.mjs"]) {
    const result = launch([path.join(import.meta.dirname, script)]);
    if (result.error || result.status !== 0) {
      summary.setup_error = `${script}: ${result.error?.message ?? `exit ${result.status}`}`;
      failed = true;
      break;
    }
  }
  async function unusedPort() {
    for (;;) {
      const server = createServer();
      await new Promise((resolve, reject) => server.once("error", reject).listen(0, "127.0.0.1", resolve));
      const port = server.address().port;
      await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
      if (![8765, 18871, 15171].includes(port)) return port;
    }
  }
  const selectedGroups = groupIndex === -1 ? groups : groups.filter(group => group.name === args[groupIndex + 1]);
  if (!failed) for (const group of selectedGroups) {
    const port = await unusedPort();
    const reportFile = path.join(root, `${group.name}.json`);
    const childEnv = { ...env, CREATOROS_E2E_PORT: String(port), PLAYWRIGHT_JSON_OUTPUT_FILE: reportFile };
    console.log(`\n=== ${group.name}: ${group.specs.length} specs, isolated API ${port} ===`);
    summary.groups[group.name] = { status: "preparing", directory: group.directory ?? "e2e", specs: group.specs, source: group.source, port, report: reportFile };
    save();
    let fixture;
    if (group.name === "media") {
      try {
        const tempRoot = path.join(repo, "tmp");
        mkdirSync(tempRoot, { recursive: true });
        const tempReal = realpathSync(tempRoot);
        if (tempReal !== path.join(realpathSync(repo), "tmp")) throw new Error("Checkout tmp must not redirect elsewhere");
        const workspace = mkdtempSync(path.join(tempReal, "media-e2e-all-"));
        const sourceRoot = path.join(workspace, "source");
        fixture = { kind: "deterministic-local", workspace, fixture_root: sourceRoot,
          source_db: path.join(sourceRoot, "review.db"), output_root: path.join(sourceRoot, "outputs"),
          server_root: path.join(workspace, "server"), test_output: path.join(workspace, "results") };
        const python = env.CREATOROS_PYTHON ?? (process.platform === "win32" ? "python" : "python3");
        const preparation = spawnSync(python, ["-m", "tests.media_collections_fixture", "--root", sourceRoot],
          { cwd: repo, env, stdio: "inherit" });
        if (preparation.error || preparation.status !== 0) throw new Error(`Fixture preparation failed: ${preparation.error?.message ?? `exit ${preparation.status}`}`);
        const produced = JSON.parse(readFileSync(path.join(sourceRoot, "fixture.json"), "utf8"));
        if (produced.source_db !== fixture.source_db || produced.output_root !== fixture.output_root
            || realpathSync(produced.source_db) !== fixture.source_db || realpathSync(produced.output_root) !== fixture.output_root
            || produced.creator_id !== "review-lab" || !Array.isArray(produced.run_ids) || new Set(produced.run_ids).size !== 2
            || !["unique_runs", "validated_images", "local_producer_calls", "workers_stopped"].every(name => produced.checks?.[name] === true)) {
          throw new Error("Fixture returned unexpected data paths or identities");
        }
        fixture.run_ids = produced.run_ids;
        fixture.checks = produced.checks;
        Object.assign(childEnv, { MEDIA_SOURCE_DB: fixture.source_db, MEDIA_OUTPUT_ROOT: fixture.output_root,
          MEDIA_TEST_ROOT: fixture.server_root, MEDIA_TEST_OUTPUT: fixture.test_output,
          MEDIA_DIST: dist, MEDIA_CREATOR_ID: produced.creator_id });
      } catch (error) {
        summary.groups[group.name] = { status: "failed", directory: group.directory ?? "e2e", specs: group.specs, source: group.source,
          port, fixture, errors: [String(error.message ?? error)] };
        failed = true;
        save();
        continue;
      }
    }
    if (prepareOnly) {
      summary.groups[group.name] = { status: "prepared", directory: group.directory ?? "e2e", specs: group.specs, source: group.source,
        port, fixture, tests_not_run: group.specs, expected: 0, unexpected: 0, skipped: 0, flaky: 0 };
      save();
      continue;
    }
    summary.groups[group.name] = { status: "running", directory: group.directory ?? "e2e", specs: group.specs, source: group.source, port, report: reportFile, fixture };
    save();
    const result = launch([path.join(import.meta.dirname, "node_modules/@playwright/test/cli.js"), "test",
      "--config", group.config, "--reporter=line,json", ...group.specs.map(name => `${group.directory ?? "e2e"}/${name}`)], childEnv);
    const entry = { status: "failed", directory: group.directory ?? "e2e", specs: group.specs, source: group.source, fixture, port, report: reportFile,
      expected: 0, unexpected: 0, skipped: 0, flaky: 0, missing_specs: group.specs };
    if (existsSync(reportFile)) {
      try {
        const report = JSON.parse(readFileSync(reportFile, "utf8"));
        const seen = new Set((report.suites ?? []).map(suite => path.basename(suite.file)));
        entry.missing_specs = group.specs.filter(name => !seen.has(name));
        entry.unexpected_specs = [...seen].filter(name => !group.specs.includes(name));
        for (const key of ["expected", "unexpected", "skipped", "flaky"]) entry[key] = report.stats?.[key] ?? 0;
        entry.errors = (report.errors ?? []).map(error => error.message ?? String(error));
        entry.status = classifyGroup({ ...entry, exitCode: result.status, launchError: result.error });
      } catch (error) {
        entry.errors = [`Invalid Playwright report: ${error.message}`];
      }
    } else entry.errors = [result.error?.message ?? "Playwright did not produce a JSON report"];
    summary.groups[group.name] = entry;
    save();
    console.log(`${group.name}: ${entry.status}; passed=${entry.expected}, failed=${entry.unexpected}, skipped=${entry.skipped}, flaky=${entry.flaky}; missing specs=${entry.missing_specs.length}`);
    if (entry.status !== "passed") failed = true;
  }
  // Notice a spec added during a long run rather than silently omitting it.
  const after = readdirSync(dir).filter(name => name.endsWith(".spec.ts")).sort();
  if (JSON.stringify(after) !== JSON.stringify(actual)) {
    summary.inventory_changed = { before: actual, after };
    failed = true;
  }
  const afterMedia = inventory(mediaDir);
  if (JSON.stringify(afterMedia) !== JSON.stringify(actualMedia)) {
    summary.media_inventory_changed = { before: actualMedia, after: afterMedia };
    failed = true;
  }
  const afterSources = sourceFiles();
  const names = new Set([...Object.keys(beforeSources), ...Object.keys(afterSources)]);
  const changed = [...names].filter(name => beforeSources[name] !== afterSources[name]).sort();
  if (changed.length) {
    summary.source_changed_paths = changed;
    failed = true;
  }
  summary.status = failed ? "failed" : prepareOnly ? "prepared_not_run" : groupIndex === -1 ? "controlled_passed_live_not_run" : "partial_passed";
  save();
  console.log(`Live specs not run: ${live.join(", ")}\nSummary: ${summaryPath}`);
  process.exitCode = failed ? 1 : 0;
}
