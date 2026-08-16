import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Dev serves on 5173 and proxies the API to the Bun server on 8020. In
 * production the Bun server serves `dist/` itself, so the app is same-origin on
 * one port - which is all `cloudflared tunnel --url http://localhost:8020`
 * needs to expose the demo to a phone.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // bind all interfaces so a phone on the LAN can reach it
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8020", changeOrigin: true },
    },
  },
  build: {
    target: "es2022",
    outDir: "dist",
    sourcemap: true,
  },
});
