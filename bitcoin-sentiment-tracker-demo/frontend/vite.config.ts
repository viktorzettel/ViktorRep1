import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

//
// Vite configuration for the Bitcoin Sentiment Tracker demo.
//
// This configuration sets up the React plugin, proxies API requests to the
// Flask back‑end during development and defines a convenient alias for
// project imports.  See the README for usage instructions.

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    host: '0.0.0.0',
    proxy: {
      // Proxy calls to the Flask API so that front‑end requests to `/api`
      // are forwarded to the back‑end running on port 5000.  This avoids
      // cross‑origin issues during local development.
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
});