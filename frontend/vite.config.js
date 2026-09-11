import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { copyFileSync, mkdirSync, readdirSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const rootDir = fileURLToPath(new URL('.', import.meta.url))

/**
 * geotiff-tilesource ships a Web Worker that decodes TIFF tiles off the main
 * thread. Its built bundle references that worker by an ABSOLUTE URL:
 *
 *     new Worker(new URL("/assets/tiff.worker-<hash>.js", import.meta.url))
 *
 * so the worker (and the decoder chunks it lazy-imports) must be served at
 * `/assets/*`. They live under node_modules and are not served by default,
 * which makes tile decoding 404 in dev (blank viewer) and breaks the
 * production build (Rollup can't resolve the worker entry). This plugin syncs
 * those prebuilt assets into `public/assets/` so they resolve at `/assets/*`
 * in both `vite dev` and `vite build`. It re-runs on every start, so it also
 * self-heals when the library is reinstalled with new content-hashed names.
 */
function copyGeoTIFFWorkerAssets() {
  const src = path.join(rootDir, 'node_modules', 'geotiff-tilesource', 'dist', 'assets')
  const dest = path.join(rootDir, 'public', 'assets')
  const sync = () => {
    if (!existsSync(src)) return
    mkdirSync(dest, { recursive: true })
    for (const file of readdirSync(src)) {
      if (file.endsWith('.js')) copyFileSync(path.join(src, file), path.join(dest, file))
    }
  }
  return {
    name: 'copy-geotiff-worker-assets',
    // Runs for both `serve` (dev) and `build`, before modules are resolved.
    buildStart: sync,
    configureServer: sync,
  }
}

/**
 * The same geotiff-tilesource bundle constructs its worker as:
 *
 *     new Worker(new URL("/assets/tiff.worker-<hash>.js", import.meta.url), ...)
 *
 * During `vite build`, Rollup/Vite's worker plugin statically detects that
 * `new URL(..., import.meta.url)` and tries to bundle the worker as its own
 * entry — which fails, because the worker itself code-splits its decoders
 * (LERC/JPEG/deflate/…) that Vite's default IIFE worker format can't emit.
 * Re-bundling would also break those lazy imports.
 *
 * We don't want Vite to touch it at all: the worker is already built and is
 * served verbatim from `public/assets/` (see copyGeoTIFFWorkerAssets). This
 * `pre` transform rewrites the `new URL(...)` into a plain string literal, so
 * `new Worker("/assets/tiff.worker-<hash>.js", ...)` stays a runtime reference
 * that Vite leaves untouched. Runs before Vite's worker plugin.
 */
function keepGeoTIFFWorkerExternal() {
  return {
    name: 'keep-geotiff-worker-external',
    enforce: 'pre',
    transform(code, id) {
      if (!id.includes('geotiff-tilesource') || !code.includes('tiff.worker-')) return null
      const replaced = code.replace(
        /new URL\(\s*(?:\/\*[^*]*\*\/\s*)?(["'])(\/assets\/tiff\.worker-[^"']+)\1\s*,\s*import\.meta\.url\s*\)/g,
        '$1$2$1',
      )
      return replaced === code ? null : { code: replaced, map: null }
    },
  }
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), copyGeoTIFFWorkerAssets(), keepGeoTIFFWorkerExternal()],
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
