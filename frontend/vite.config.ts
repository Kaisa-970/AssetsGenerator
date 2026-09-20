import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  test: { include: ["src/**/*.test.ts"] },
  base: "./",
  build: {
    outDir: "../src/assets_generator/resources/node-editor",
    emptyOutDir: true,
  },
  server: { proxy: { "/api": "http://127.0.0.1:8767" } },
});
