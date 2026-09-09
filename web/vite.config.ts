import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 8000/8001 若被系统僵尸监听占用，默认走 8010（与 start-backend.ps1 一致）
      '/api': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
      '/dash': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
      '/chat': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
      '/models': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
      '/agents': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
      '/storage': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
      '/market': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
      '/skills': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
      '/strategy': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
      '/data': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
    },
  },
})
