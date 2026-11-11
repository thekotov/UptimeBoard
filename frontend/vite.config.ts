import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, proxy /api to the backend so EventSource/SSE stays same-origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        // VITE_API_TARGET=https://status.example.com — смотреть вёрстку на живых данных
        target: process.env.VITE_API_TARGET || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
