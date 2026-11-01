import { defineConfig, devices } from "@playwright/test";

// Drives the real `proofmerge dashboard` server (Python) over fixture evidence. Only gh,
// git and verify are faked (e2e/serve.py), so the API, SSE and security checks are real.
const PORT = 8798;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `python e2e/serve.py --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/v1/session`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
