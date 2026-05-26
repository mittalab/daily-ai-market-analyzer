import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'https://api.abhishekmittal.in',
        changeOrigin: true,
        secure: true,
      },
    },
  },
});
