import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:9125", trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.PLAYWRIGHT_BASE_URL ? undefined : {
    command: "cp -a dist/. ../solarmax/static/webui/ && cd .. && SOLARMAX_WEBUI_MODE=react SOLARMAX_DB_PATH=/tmp/solarmax-webui-e2e.db uvicorn solarmax.main:app --host 127.0.0.1 --port 9125",
    url: "http://127.0.0.1:9125/",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
