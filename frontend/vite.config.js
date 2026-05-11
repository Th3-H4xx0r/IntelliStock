import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// Root of the repo (one level up from frontend/)
const rootDir = fileURLToPath(new URL('..', import.meta.url))

export default defineConfig(({ mode }) => {
  // Read .env from the repo root — not from frontend/
  const env = loadEnv(mode, rootDir, '')
  const apiTarget = env.VITE_API_URL || 'http://localhost:8000'

  return {
    plugins: [vue()],

    // Tell Vite to look for .env files in the repo root
    envDir: rootDir,

    server: {
      proxy: {
        // All /api/* requests are proxied to the backend — avoids CORS in dev.
        // Target is read from VITE_API_URL in the root .env
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },

    build: {
      // Source maps would let anyone deobfuscate the production bundle.
      // Keep them off for the public deploy; emit them locally if you
      // need to debug a prod issue (set VITE_SOURCEMAP=1 and rebuild).
      sourcemap: env.VITE_SOURCEMAP === '1' || env.VITE_SOURCEMAP === 'true',
    },
  }
})
