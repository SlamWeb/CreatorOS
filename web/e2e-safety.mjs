import path from "node:path";

// Prevent an inherited opt-in for paid/live probes or a foreign instance from
// changing the ordinary regression run. This is not a network sandbox.
export function controlledEnvironment(source, root, dist) {
  const env = { ...source };
  const exact = new Set(["AGENT_BASELINE", "CREATOROS_ASSERT_RAW_REFERENCE_TURN",
    "CREATOROS_INTERNAL_E2E_ROOT", "CREATOROS_E2E_ROOT", "CREATOROS_E2E_PORT", "CREATOROS_E2E_VITE_PORT",
    "CREATOROS_API_URL", "CREATOROS_STUDIO_URL", "CREATOROS_CONTEXT_FOCUS_ROOT", "CREATOROS_MEDIA_AGENT_ROOT",
    "CREATOROS_BATCH_IMAGES", "MEDIA_AGENT_BASE_URL", "CREATOROS_CALIBRATION_OUTPUT"]);
  const allowedCreatoros = new Set(["CREATOROS_PYTHON", "CREATOROS_FFMPEG", "CREATOROS_FFPROBE"]);
  for (const key of Object.keys(env)) {
    const name = key.toUpperCase();
    if (exact.has(name) || name.startsWith("MEDIA_") || /(TOKEN|SECRET|PASSWORD|PASSWD|COOKIE|CREDENTIAL|API_KEY|PRIVATE_KEY)/.test(name)
        || (name.startsWith("CREATOROS_") && !allowedCreatoros.has(name))) delete env[key];
  }
  Object.assign(env, {
    DEEPSEEK_API_KEY: "", OPENAI_API_KEY: "", CODEX_API_KEY: "", ZHIHU_ACCESS_SECRET: "",
    PERSONCLONE_SESSION_COOKIE: "", PERSONCLONE_BASE_URL: "http://127.0.0.1:1",
    CREATOROS_XHS_MCP_TOKEN: "", CREATOROS_XHS_MCP_TOKEN_FILE: path.join(root, "no-publication-token"),
    CREATOROS_XHS_MCP_URL: "http://127.0.0.1:1/mcp", CREATOROS_XHS_STAGING_ROOT: path.join(root, "publication-staging"),
    CODEX_HOME: path.join(root, "codex-home"), CREATOROS_CODEX_EXECUTABLE: path.join(root, "no-codex.exe"),
    CREATOROS_POWERSHELL_EXECUTABLE: path.join(root, "no-powershell.exe"),
    CREATOROS_MEDIA_DOWNLOADER: path.join(root, "no-downloader.exe"),
    PYTHON_DOTENV_DISABLED: "1",
    CREATOROS_BUILD_OUT_DIR: dist, CREATOROS_E2E_DIST: dist, STAGED_UI_DIST: dist, PRODUCTION_TASKS_DIST: dist,
  });
  return env;
}
