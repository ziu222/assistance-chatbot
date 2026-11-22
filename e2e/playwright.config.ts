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
  // Cửa sổ gom tin ngắn để test không phải chờ 6 giây.
  AGENT_DEBOUNCE_WINDOW_SECONDS: "0.5",
  AGENT_DEBOUNCE_MAX_WAIT_SECONDS: "2",
} as Record<string, string>;

// Agent worker cần cấu hình LLM hợp lệ để khởi động, nhưng e2e chỉ đi đường rule (câu chào) nên
// không bao giờ gọi mạng. Key giả + base_url chết bảo đảm không thể tốn tiền thật, kể cả khi
// backend/.env có key thật.
const agentEnv = {
  ...env,
  CLAUDE_API_KEY: "e2e-unused",
  OPENAI_API_KEY: "e2e-unused",
  LLM_BASE_URL: "http://127.0.0.1:9",
};

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
      command: "uv run python -m app.entrypoints.agent_worker",
      cwd: "../backend",
      port: 9102, // /metrics của agent worker: sẵn sàng khi worker đã khởi động
      env: agentEnv,
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
