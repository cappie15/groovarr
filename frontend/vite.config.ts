import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Groovarr frontend is built to static assets and served by the backend
// container (see docs/00-research-and-architecture-review.md §4/§5) — the
// backend serves the SPA and exposes the API under same-origin `/api`, so no
// dev-time proxy target is hardcoded to a specific host, just the path.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      // Matches the backend's actual default PORT (app/core/config.py,
      // .env.example) — was pointed at :8000 in the Phase 1 scaffold, which
      // never matched. `/health` is proxied too: it's a real backend
      // endpoint the System/Status page calls, deliberately outside `/api`.
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
});
