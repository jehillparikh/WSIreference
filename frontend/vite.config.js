import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy all /api/* calls to the FastAPI backend during dev
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        rewrite: (path) => path,
      },
    },
  },
  build: {
    // Output into ../frontend/dist so FastAPI can serve it as StaticFiles
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor:       ['react', 'react-dom', 'react-router-dom'],
          openseadragon:['openseadragon'],
          query:        ['@tanstack/react-query'],
        },
      },
    },
  },
})
