import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/tenants': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/vehicles': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/knowledge-base': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/unknown-faults': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
});
