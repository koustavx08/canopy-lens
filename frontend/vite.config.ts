/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dev server proxies /api to the FastAPI backend so the browser never
// needs to know about ports or CORS during development.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_PROXY_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    // jsdom because the things worth testing here are the things that touch
    // the DOM: what the interface renders when a figure is missing, and what
    // the hook does while an analysis is in flight.
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // Tests live beside the code they cover, so a component and its test move
    // together and neither can be found without the other.
    include: ["src/**/*.test.{ts,tsx}"],
    // Tailwind's Vite plugin would compile the whole stylesheet for every test
    // file. Nothing here asserts on a computed style.
    css: false,
  },
});
