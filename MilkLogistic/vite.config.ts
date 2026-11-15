import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  // Docker/Electron — корень, GitHub Pages — подпапка репозитория
  base: process.env.VITE_BASE || '/',
  plugins: [react()],
  server: {
    // Локальная разработка: запросы /api проксируются на бэкенд, CORS не нужен
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
