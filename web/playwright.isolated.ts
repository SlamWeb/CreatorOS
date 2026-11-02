import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

// Each invocation owns its files. Explicit ports allow concurrent worktrees;
// an occupied port fails rather than reusing an unrelated server.
export function isolatedBrowserRun(prefix: string, defaultPort: number) {
  const port = Number(process.env.CREATOROS_E2E_PORT ?? defaultPort);
  if (!Number.isInteger(port) || port < 1 || port > 65535 || port === 8765) {
    throw new Error("CREATOROS_E2E_PORT must be a valid non-production port");
  }
  const firstLoad = !process.env.CREATOROS_INTERNAL_E2E_ROOT;
  const root = process.env.CREATOROS_INTERNAL_E2E_ROOT ?? mkdtempSync(path.join(tmpdir(), `creatoros-${prefix}-`));
  const data = path.join(root, "data");
  const temp = path.join(root, "temp");
  mkdirSync(data, { recursive: true });
  mkdirSync(temp, { recursive: true });
  delete process.env.AGENT_BASELINE;
  // Browser processes and worker-side temporary files also stay in this run.
  Object.assign(process.env, { CREATOROS_INTERNAL_E2E_ROOT: root, TEMP: temp, TMP: temp, TMPDIR: temp });
  if (firstLoad) console.log(`Isolated browser data and results: ${root}`);
  return {
    port, root, data,
    url: `http://127.0.0.1:${port}`,
    output: path.join(root, "test-results"),
    // Playwright inherits the process environment for webServer itself. Passing
    // it here as well would serialize every secret into the JSON report.
    env: { CREATOROS_E2E_ROOT: data, CREATOROS_STUDIO_URL: `http://127.0.0.1:${port}`,
      TEMP: temp, TMP: temp, TMPDIR: temp,
      DEEPSEEK_API_KEY: "", OPENAI_API_KEY: "", CODEX_API_KEY: "", ZHIHU_ACCESS_SECRET: "",
      PYTHON_DOTENV_DISABLED: "1", CODEX_HOME: path.join(root, "codex-home"),
      CREATOROS_CODEX_EXECUTABLE: path.join(root, "no-codex.exe"),
      CREATOROS_POWERSHELL_EXECUTABLE: path.join(root, "no-powershell.exe"),
      CREATOROS_MEDIA_DOWNLOADER: path.join(root, "no-downloader.exe"),
      PERSONCLONE_SESSION_COOKIE: "", PERSONCLONE_BASE_URL: "http://127.0.0.1:1",
      CREATOROS_XHS_MCP_TOKEN: "", CREATOROS_XHS_MCP_URL: "http://127.0.0.1:1/mcp",
      CREATOROS_XHS_MCP_TOKEN_FILE: path.join(root, "no-publication-token"),
      CREATOROS_XHS_STAGING_ROOT: path.join(root, "publication-staging") },
  };
}
