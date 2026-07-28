import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // BASE resolves to window.location.origin (:5173) since VITE_API_URL
      // is unset, so all API calls are same-origin. Forward them to FastAPI
      // on :8000. Without this, requests 404 against the Vite dev server.
      '/api': { target: 'http://localhost:8000', ws: true },
      '/auth': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/inspect': 'http://localhost:8000',
    },
  },
})
