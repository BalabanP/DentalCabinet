import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./" lets the built files work from any sub-path (e.g. CloudFront /admin/).
// Locally the browser only talks to /api, which Vite forwards to the local backend (backend/local/server.py).
export default defineConfig({
  base: "./",
  plugins: [react()],
  // amazon-cognito-identity-js refers to Node's `global`, which browsers do not have.
  define: { global: "globalThis" },
  server: {
    port: 5174,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
