import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    port: 5173,
    // 开发时把 API 请求转给 FastAPI（生产由 FastAPI 直接托管构建产物，同源）
    proxy: { '/api': { target: 'http://localhost:8080', changeOrigin: true } },
  },
})
