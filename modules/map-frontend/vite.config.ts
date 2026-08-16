import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Dev only. Keeps API_BASE = "" correct everywhere: same-origin in prod
  // (synthesis serves dist/, SPEC.md §6.8), and proxied to synthesis's port
  // while Vite is serving on 5173. USE_MOCK stays true in config.ts until
  // synthesis's /api/route exists, so this proxy is inert for now.
  server: {
    host: true,
    proxy: {
      "/api": "http://localhost:8020",
      "/health": "http://localhost:8020",
    },
  },
  build: { target: "es2020" },
});
