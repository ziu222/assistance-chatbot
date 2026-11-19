import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// App trong iframe. Backend phục vụ /widget/app.js + /widget/app.css cho trang /w/{key}.
// ponytail: tên file cố định + Cache-Control: no-cache; đổi sang tên có hash + manifest khi
// cần cache dài hạn.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2020",
    rollupOptions: {
      input: "src/app/main.tsx",
      output: {
        entryFileNames: "app.js",
        assetFileNames: "app.[ext]",
        inlineDynamicImports: true,
      },
    },
  },
  test: { environment: "node" },
});
