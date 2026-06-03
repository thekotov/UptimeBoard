import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, proxy /api to the backend so EventSource/SSE stays same-origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
