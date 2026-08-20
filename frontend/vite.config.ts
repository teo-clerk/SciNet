import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // Keeps the browser on a single origin in dev; the API stays bound to
      // 127.0.0.1 and is never exposed on the network.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
})
