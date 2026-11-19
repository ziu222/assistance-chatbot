import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev: trình duyệt chỉ nói chuyện với origin của Vite (5173); /api được proxy sang backend.
// Cùng origin => cookie __Host-sid hoạt động, CSRF Origin = STAFF_ORIGIN (http://localhost:5173).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: false } },
  },
  test: { environment: "node" },
});
