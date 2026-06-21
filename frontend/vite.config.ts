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
    rollupOptions: {
      output: {
        // Split rarely-changing vendor libs into their own chunks for caching
        // and to clear the >500 kB single-bundle warning.
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          mantine: ["@mantine/core", "@mantine/hooks", "@mantine/notifications"],
          query: ["@tanstack/react-query", "@tanstack/react-table"],
          dnd: ["@dnd-kit/core"],
        },
      },
    },
  },
});
