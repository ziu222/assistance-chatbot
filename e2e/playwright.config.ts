import { randomBytes } from "node:crypto";
import { defineConfig } from "@playwright/test";

// Dựng toàn bộ hệ thống thật: API, relay worker, trang host mẫu (widget nhúng), admin inbox.
// Cần Postgres + Redis đang chạy (docker compose / service của CI) và widget đã build.
const CI = !!process.env.CI;
const env = {
  ...process.env,
  WEBCHAT_TOKEN_SECRETS: JSON.stringify({ k1: randomBytes(32).toString("base64url") }),
  PUBLIC_ORIGIN: "http://localhost:8000",
  STAFF_ORIGIN: "http://localhost:5173",
} as Record<string, string>;

export default defineConfig({
  testDir: "tests",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: CI ? 1 : 0,
  reporter: CI ? [["list"], ["html", { open: "never" }]] : "list",
  globalSetup: "./global-setup.ts",
  use: {
    // Local dùng Chrome đã cài (không tải trình duyệt); CI cài chromium của Playwright.
    channel: CI ? undefined : "chrome",
    locale: "vi-VN",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "uv run uvicorn app.entrypoints.api:app --port 8000",
      cwd: "../backend",
      url: "http://localhost:8000/health",
      env,
      reuseExistingServer: !CI,
    },
    {
      command: "uv run python -m app.entrypoints.worker",
      cwd: "../backend",
      port: 9101, // /metrics của worker: sẵn sàng khi relay đã chạy
      env,
      reuseExistingServer: !CI,
    },
    {
      command: "uv run python -m http.server 5500 -d ../widget/demo",
      cwd: "../backend",
      url: "http://localhost:5500",
      reuseExistingServer: !CI,
    },
    {
      command: "npm run dev",
      cwd: "../frontend",
      url: "http://localhost:5173",
      reuseExistingServer: !CI,
    },
  ],
});
