import { defineConfig } from "@playwright/test";
const port = Number(process.env.EDITOR_E2E_PORT || "5174");
export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: `http://127.0.0.1:${port}`, headless: true },
  webServer: {
    command: `npm run dev -- --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
  },
});
