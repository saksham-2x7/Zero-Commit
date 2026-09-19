import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'https://d4uzp0bsghs2h.cloudfront.net',
        changeOrigin: true
      }
    }
  }
})
