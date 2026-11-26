import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/feed.atom': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/feeds': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/join.txt': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/join.sh': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/llms.txt': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/mcp': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/.well-known': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '^/.*\\.md$': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
});
