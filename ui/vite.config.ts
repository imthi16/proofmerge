/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build is served by `proofmerge dashboard` from the Python package, so no Node is
// needed at runtime. Assets live under /static/app/; the server answers every
// Control Center route (/, /runs/..., /pull-requests, ...) with index.html.
const target = process.env.PROOFMERGE_DASHBOARD ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  base: "/static/app/",
  build: {
    outDir: fileURLToPath(new URL("../src/proofmerge/dashboard_static/app", import.meta.url)),
    emptyOutDir: true,
    sourcemap: false,
    // Stable names would cache badly; hashed names are fine because the page is no-store.
    chunkSizeWarningLimit: 400,
  },
  server: {
    // Development only: forward the API to a running `proofmerge dashboard`. The server
    // checks Host and Origin, so the proxy presents itself as the dashboard's own origin.
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("proxyReq", (request) => {
            if (request.getHeader("origin")) request.setHeader("origin", target);
          });
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
