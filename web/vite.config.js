import { defineConfig } from "vite";

// Serve the Python fetcher's output (../data) as static files, so
// /manifest.json and /<layer>.geojson resolve in dev and get copied into builds.
export default defineConfig({
  publicDir: "../data",
});
