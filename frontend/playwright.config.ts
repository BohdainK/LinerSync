import { defineConfig, devices } from "@playwright/test";

const BASE_URL = "http://127.0.0.1:5173";

// One fresh fake world per run (D14): the e2e server makes a new data folder on start.
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: BASE_URL,
    // Written by the global setup; the onboarding spec opts out to sign in itself.
    storageState: "e2e/.auth.json",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "uv run python -m tests.e2e_server --port 8765 --public-url http://127.0.0.1:5173",
      cwd: "../backend",
      url: "http://127.0.0.1:8765/api/health",
      reuseExistingServer: false,
      // SIGTERM lets the server remove its temporary data folder (Playwright's default is SIGKILL).
      gracefulShutdown: { signal: "SIGTERM", timeout: 5000 },
    },
    {
      command: "npm run dev",
      env: { LINERSYNC_API: "http://127.0.0.1:8765" },
      url: BASE_URL,
      reuseExistingServer: false,
    },
  ],
});
