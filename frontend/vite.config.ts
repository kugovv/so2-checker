import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: './',
  plugins: [react()],
  build: { target: ['chrome100', 'safari15.4'], sourcemap: false },
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
});
