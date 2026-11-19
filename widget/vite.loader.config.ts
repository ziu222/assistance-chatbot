import { defineConfig } from "vite";

// loader.js chạy trên trang của khách hàng: một file IIFE, không dependency, không ghi đè dist.
export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: false,
    target: "es2018",
    lib: {
      entry: "src/loader/loader.ts",
      formats: ["iife"],
      name: "AtlasWebchatLoader",
      fileName: () => "loader.js",
    },
  },
});
