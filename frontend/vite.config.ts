import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The browser only talks to /api; Vite forwards it to the Python chat server (which holds the LLM key).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8100",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
