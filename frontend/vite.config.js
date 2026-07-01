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
        // Preserve all request headers so Range requests for tile streaming work.
        // Without this, http-proxy may strip the Range header, breaking GeoTIFFTileSource.
        headers: {},
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq, req) => {
            // Forward Range header explicitly (required for byte-range tile fetches)
            const range = req.headers['range']
            if (range) proxyReq.setHeader('Range', range)
          })
        },
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
          vendor:        ['react', 'react-dom', 'react-router-dom'],
          openseadragon: ['openseadragon'],
          geotiff:       ['geotiff-tilesource'],
          query:         ['@tanstack/react-query'],
        },
      },
    },
  },
})
