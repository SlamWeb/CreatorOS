import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

const apiUrl = process.env.CREATOROS_API_URL;
if (apiUrl) {
  const api = new URL(apiUrl);
  if (api.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(api.hostname)
      || api.username || api.password || api.pathname !== "/" || api.search || api.hash) {
    throw new Error("CREATOROS_API_URL must be a local HTTP origin");
  }
}
const vitePort = Number(process.env.CREATOROS_VITE_PORT ?? "5173");
if (!Number.isInteger(vitePort) || vitePort < 1 || vitePort > 65535) {
  throw new Error("CREATOROS_VITE_PORT must be a valid port");
}

export default defineConfig(({ command, isPreview }) => {
  if (command === "serve" && !isPreview && !apiUrl) {
    throw new Error("未指定 API。请在仓库根目录运行：python -m scripts.isolated_dev --root tmp/dev-18871 --api-port 18871 --vite-port 15171（目录须是新的）；或在 PowerShell 中设置 $env:CREATOROS_API_URL='http://127.0.0.1:18871' 后运行 npm run dev。");
  }
  return {
    plugins: [react(), tailwindcss()],
    cacheDir: process.env.CREATOROS_VITE_CACHE_DIR,
    ...(process.env.CREATOROS_BUILD_OUT_DIR ? { build: { outDir: process.env.CREATOROS_BUILD_OUT_DIR, emptyOutDir: false } } : {}),
    resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
    server: {
      host: "127.0.0.1",
      port: vitePort,
      strictPort: true,
      proxy: apiUrl ? {
        "/api": {
          target: apiUrl,
          changeOrigin: false,
        },
        ...(process.env.CREATOROS_PROXY_TEST_ROUTES === "1" ? { "/test": { target: apiUrl, changeOrigin: false } } : {}),
      } : undefined,
    },
    preview: {
      host: "127.0.0.1",
      port: 4173,
      strictPort: true,
    },
  };
});
