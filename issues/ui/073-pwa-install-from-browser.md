# [UI-073] Make Flowstate UI installable as a PWA from the browser

## Domain
ui

## Status
todo

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: SHARED-008 (UI bundled in wheel — already done)
- Blocks: —

## Spec References
- specs.md §13.4 Deployment & Installation (UI packaging)

## Summary
Add a PWA (Progressive Web App) manifest + service worker to the Flowstate React UI so users can "Install Flowstate" from Chrome / Edge / Safari's address bar. Produces a standalone window without browser chrome — feels like an app without any native packaging work. Zero changes to the Python side; the wheel already ships the built UI (SHARED-008) and the server already serves it (SERVER-032). This is the cheapest path to "Flowstate is an app" — and a useful stepping stone before committing to the full Tauri menubar app (UI-074).

## Acceptance Criteria
- [ ] `ui/public/manifest.webmanifest` declares the PWA: name `"Flowstate"`, short_name `"Flowstate"`, `display: "standalone"`, `start_url: "/"`, `theme_color` matching the dark theme, `background_color`, and an icons array (192x192, 512x512, maskable variants).
- [ ] `ui/public/icons/` contains the icon files at the required sizes. Reuse `logo.png` / `logo-light.png` if they're square; else commission/generate appropriate icons.
- [ ] `ui/index.html` references the manifest in `<head>` via `<link rel="manifest" href="/manifest.webmanifest">` and adds Apple-specific meta (`apple-mobile-web-app-capable`, `apple-mobile-web-app-title`, `apple-touch-icon`).
- [ ] A minimal service worker registers via Vite's PWA plugin (`vite-plugin-pwa`) — caches the app shell (`index.html` + `assets/*`) so the UI loads even if the server is briefly unreachable. **Do NOT** cache `/api/*` or `/ws` — those must always go to the network.
- [ ] When opened in Chrome, an "Install Flowstate" prompt appears in the address bar. After install, the app opens in a standalone window with a Flowstate icon in the dock/taskbar.
- [ ] After install, the app's title bar shows "Flowstate" (not the browser tab title).
- [ ] `npm run build` succeeds and the manifest + service worker land in `ui/dist/`. Confirmed by re-running `uv build --wheel && unzip -l dist/*.whl | grep -E "manifest|sw"`.
- [ ] No regression on the existing browser experience — opening `http://127.0.0.1:9090/` in a regular tab still works identically.

## Technical Design

### Files to Create/Modify
- `ui/package.json` — add `vite-plugin-pwa` to devDependencies.
- `ui/vite.config.ts` — register the plugin with `VitePWA({...})`.
- `ui/public/manifest.webmanifest` — the PWA manifest.
- `ui/public/icons/icon-192.png`, `icon-512.png`, `icon-192-maskable.png`, `icon-512-maskable.png` — icon assets.
- `ui/index.html` — `<link rel="manifest">` and Apple meta tags.

### Key Implementation Details

**`vite.config.ts`:**
```ts
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      manifest: false,                 // we ship our own manifest.webmanifest
      workbox: {
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//, /^\/ws/, /^\/health/],
        runtimeCaching: [
          // Never cache the API
          { urlPattern: /^\/api\//,    handler: "NetworkOnly" },
          { urlPattern: /^\/ws/,       handler: "NetworkOnly" },
          { urlPattern: /^\/health/,   handler: "NetworkOnly" },
        ],
      },
    }),
  ],
});
```

**`manifest.webmanifest`:**
```json
{
  "name": "Flowstate",
  "short_name": "Flowstate",
  "description": "State-machine orchestration for AI agents",
  "start_url": "/",
  "display": "standalone",
  "theme_color": "#0a0a0a",
  "background_color": "#0a0a0a",
  "icons": [
    { "src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png" },
    { "src": "/icons/icon-192-maskable.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable" },
    { "src": "/icons/icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ]
}
```

### Edge Cases
- **`/health` and `/api/*` must never be served from cache** — agents need real responses. The `NetworkOnly` runtime caching rules above enforce this; verify with DevTools → Application → Service Workers.
- **Service worker scope** — Flowstate is served at `/`, so the SW scope is `/`. No multi-tenant concerns since each project has its own server on its own port.
- **Updates** — `registerType: "autoUpdate"` means the SW silently updates when a new version is detected; users get the new UI on next page load. No "refresh to update" banner needed for v0.1.
- **Apple Safari quirks** — Apple requires the `apple-mobile-web-app-capable` meta tag and an `apple-touch-icon`. Without these, Safari's "Add to Home Screen" produces a non-fullscreen shortcut.
- **Maskable icons** — Android requires both regular and maskable variants; without them the icon is shown in a small white box.

## Testing Strategy
- Manual: open `http://127.0.0.1:9090` in Chrome after `npm run build && uv run flowstate server`. Confirm the address bar shows the install icon. Click it, install, confirm the standalone window opens with the Flowstate icon in the dock.
- Manual: confirm the same in Safari (macOS): File → "Add to Dock" produces a dock app.
- Build sanity: `cd ui && npm run build && ls dist/manifest.webmanifest dist/sw.js dist/icons/` exists.
- API regression: with the SW installed, hit `/api/flows` — DevTools network tab should show it as "Service Worker" + "Network" (not "from cache").

## E2E Verification Plan

### Verification Steps
1. Build: `cd ui && npm install && npm run build`.
2. Start: `cd .. && uv run flowstate server` (with the dev-repo `flowstate.toml`).
3. Open `http://127.0.0.1:9090` in a fresh Chrome profile.
4. Address bar shows "Install Flowstate" icon. Click → confirm. Window pops out with no browser chrome.
5. Quit Chrome. Re-launch the standalone Flowstate window from Spotlight / Launchpad. UI loads instantly (cached app shell), then fetches live data.
6. Network test: in DevTools while in the standalone window, force offline. UI shell still renders (cached); API calls fail with the expected error states.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `vite-plugin-pwa` added + configured
- [ ] `manifest.webmanifest` shipped in `ui/dist/`
- [ ] Icons present at all required sizes (regular + maskable)
- [ ] `index.html` references manifest + Apple meta
- [ ] SW caches app shell, never caches `/api/*` / `/ws` / `/health`
- [ ] Chrome install flow verified
- [ ] Safari "Add to Dock" verified
- [ ] No regression in regular-tab usage
- [ ] `cd ui && npm run lint` passes
