import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Ports are env-driven: APP_PORT (served UI, prod preview) and DEV_PORT (dev server).
const APP_PORT = Number(process.env.APP_PORT || 8080);
const DEV_PORT = Number(process.env.DEV_PORT || 5173);
const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: DEV_PORT,
    strictPort: true,
    proxy: { "/api": process.env.BACKEND_URL || "http://127.0.0.1:8000" },
  },
  preview: {
    host: "127.0.0.1",
    port: APP_PORT,
    strictPort: true,
    proxy: { "/api": { target: BACKEND, changeOrigin: true } },
  },
});
