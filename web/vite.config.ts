/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const target = process.env.LAB_BACKEND ?? 'http://127.0.0.1:8000'

// Dev server proxies the API + websockets to the Python server; production is served by FastAPI itself.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/ws': { target, ws: true },
      '/api': target,
      '/labels': target,
      '/features': target,
      '/calls': target,
      '/health': target,
    },
  },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
