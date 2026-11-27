import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  // Point the dev proxy at another hub (for example production, read-only):
  // VITE_HUB_PROXY_TARGET=https://aiwatchdogs.in npm run dev
  const HUB = loadEnv(mode, '.', 'VITE_').VITE_HUB_PROXY_TARGET || 'http://127.0.0.1:8000';

  return {
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      proxy: {
        '/api': {
          target: HUB,
          changeOrigin: true,
        },
        '/feed.atom': { target: HUB, changeOrigin: true },
        '/graveyard.atom': { target: HUB, changeOrigin: true },
        '/feeds': { target: HUB, changeOrigin: true },
        '/join.txt': {
          target: HUB,
          changeOrigin: true,
        },
        '/join.sh': {
          target: HUB,
          changeOrigin: true,
        },
        '/health': {
          target: HUB,
          changeOrigin: true,
        },
        '/llms.txt': {
          target: HUB,
          changeOrigin: true,
        },
        '/mcp': {
          target: HUB,
          changeOrigin: true,
        },
        '/.well-known': {
          target: HUB,
          changeOrigin: true,
        },
        '^/.*\\.md$': {
          target: HUB,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
      sourcemap: false,
    },
  };
});
