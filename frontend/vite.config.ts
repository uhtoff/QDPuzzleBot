import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// During development the SPA runs on Vite's dev server and proxies /api (and the
// activity WebSocket) to the Quart server embedded in the bot on port 8099.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8099",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
