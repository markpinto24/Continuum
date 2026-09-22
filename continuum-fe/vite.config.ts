import path from 'node:path'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
// vitest's defineConfig, not vite's — it is the one that knows about `test`.
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
    // Exactly one three.js, always. The graph renderer builds scene objects in
    // three-forcegraph and draws them in three-render-objects; if those resolve
    // to different copies, the render loop throws `intersectsFrustum is not a
    // function` every frame and the canvas stays blank with no visible error.
    dedupe: ['three'],
  },
  build: {
    rollupOptions: {
      output: {
        // three.js and the force-graph renderer are ~85% of the bundle and
        // change only when we bump them. Splitting them out means app edits
        // ship a small chunk and the big one stays in the browser cache.
        manualChunks: {
          graph: ['three', 'react-force-graph-3d'],
          // The Markdown parser plus highlight.js's language grammars — another
          // large, rarely-changing block that should not ride along with app edits.
          markdown: ['react-markdown', 'remark-gfm', 'rehype-highlight'],
        },
      },
    },
    chunkSizeWarningLimit: 1800,
  },
  server: {
    port: 5173,
    // Dev-only proxy so the browser talks to one origin and CORS never enters
    // the picture. In Docker the web container is served by nginx, which
    // proxies /api to the api service the same way — see nginx.conf.
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
