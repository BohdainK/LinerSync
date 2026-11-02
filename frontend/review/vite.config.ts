// Builds review/index.html into one HTML file with the JS and CSS inlined (see inline.mjs).
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const frontend = path.resolve(import.meta.dirname, "..");

export default defineConfig({
  root: import.meta.dirname,
  base: "./",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.join(frontend, "src") } },
  build: {
    outDir: path.join(frontend, "dist-review"),
    emptyOutDir: true,
    assetsInlineLimit: Number.MAX_SAFE_INTEGER,
    cssCodeSplit: false,
  },
});
