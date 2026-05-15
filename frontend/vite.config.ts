import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Vite dev proxy: any /api, /ask, /history, /settings request goes to the
// FastAPI backend on :8080. The React app calls relative URLs so the same
// code works in dev (proxied) and prod (same-origin).
const BACKEND = process.env.SATORI_BACKEND_URL || "http://localhost:8080";

export default defineConfig({
  plugins: [react()],
  base: "/app/",                 // production assets served at /app/*
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/api":      BACKEND,
      "/ask":      BACKEND,
      "/history":  BACKEND,
      "/settings": BACKEND,
      "/static":   BACKEND,  // TMC logo + branding assets served by FastAPI
    },
  },
  build: {
    outDir: "dist",
    assetsDir: "assets",
    sourcemap: true,
  },
});
