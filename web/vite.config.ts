import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The dev server proxies /api to the FastAPI process documented in
// docs/OPERATIONS.md (`uvicorn api.app:app --host 127.0.0.1 --port 8899`).
//
// A proxy rather than an absolute URL in the client, for two reasons. The
// browser sees one origin, so there is no CORS to configure and no preflight
// on every request. And the API token stays out of the bundle: it is attached
// by the proxy from the server's own environment, so it never reaches
// JavaScript that any viewer can read. A token shipped to the browser is a
// published token.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8899',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        configure: (proxy) => {
          const token = process.env.SPX_API_TOKEN
          if (token) {
            proxy.on('proxyReq', (req) => req.setHeader('X-API-Token', token))
          }
        },
      },
    },
  },
})
