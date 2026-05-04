import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

// PWA setup (UI-073). The manifest is shipped as a real file at
// ui/public/manifest.webmanifest so we set `manifest: false` to tell the
// plugin not to generate one inline. Workbox is configured to cache the
// app shell for offline launch but to NEVER cache /api/*, /ws, or /health
// — those resources change continuously and must always hit the network.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      injectRegister: 'auto',
      manifest: false,
      // The manifest lives in public/ and is copied verbatim — declare it
      // here so Workbox includes it in the precache.
      includeAssets: [
        'manifest.webmanifest',
        'icons/icon-192.png',
        'icons/icon-512.png',
        'icons/icon-192-maskable.png',
        'icons/icon-512-maskable.png',
        'icons/apple-touch-icon.png',
      ],
      workbox: {
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//, /^\/ws/, /^\/health/],
        runtimeCaching: [
          // Runtime endpoints — always go to the network, never cache.
          { urlPattern: /^\/api\//, handler: 'NetworkOnly' },
          { urlPattern: /^\/ws/, handler: 'NetworkOnly' },
          { urlPattern: /^\/health/, handler: 'NetworkOnly' },
        ],
      },
    }),
  ],
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true,
      },
    },
  },
});
