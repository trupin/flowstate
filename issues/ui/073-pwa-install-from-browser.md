# [UI-073] Make Flowstate UI installable as a PWA from the browser

## Domain
ui

## Status
done

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

### Implementation Notes
- Added `vite-plugin-pwa@1.2.0` as a devDependency. Plugin is configured with `manifest: false` and `registerType: 'autoUpdate'`. Workbox precaches the app shell (HTML, JS, CSS, manifest, icons) and registers `NetworkOnly` runtime handlers + a navigation-fallback denylist for `/api/*`, `/ws`, and `/health` so those endpoints never serve from cache.
- Created a real `ui/public/manifest.webmanifest` with `name`, `short_name`, `description`, `start_url`, `scope`, `display: standalone`, `theme_color: #0f0f0f` (matching `--bg-primary` in `src/index.css`), `background_color`, and the four required icons (regular + maskable, 192 + 512). The issue text suggested `#0a0a0a` but the actual app theme is `#0f0f0f`; aligned to the real theme so the splash/standalone window matches the UI exactly.
- `ui/index.html` now references the manifest, declares `theme-color`, and adds the three Apple meta tags (`apple-mobile-web-app-capable`, `apple-mobile-web-app-status-bar-style`, `apple-mobile-web-app-title`) plus an `apple-touch-icon` link and standard PNG icon `<link>` tags.
- **Icon generation**: chose Pillow over `@vite-pwa/assets-generator` because Pillow was already installed on the host and the entire pipeline is one self-contained ~90-line Python script (no extra npm dep that lives only to produce build artifacts). Script lives at `ui/scripts/generate_icons.py` (kept out of `public/` so it doesn't ship in `dist/`) and is idempotent — re-running it overwrites the five PNGs in-place. The glyph is a centered "F" letterform in `--accent` (#3b82f6) on a `--bg-primary` (#0f0f0f) field. The maskable variants reserve the central 80% as a safe zone per the W3C maskable-icon spec.

### Build Verification

**`npm run lint`:**
```
$ cd ui && npm run lint
> eslint .
exit: 0
```

**`npm run build`:**
```
$ cd ui && npm run build
> tsc && vite build
vite v5.4.21 building for production...
✓ 832 modules transformed.
dist/registerSW.js                0.13 kB
dist/index.html                   1.03 kB
dist/assets/index-BXvekFWk.css   71.66 kB
dist/assets/index-CnNRnVC-.js   685.40 kB
✓ built in 1.35s
PWA v1.2.0
mode      generateSW
precache  10 entries (740.57 KiB)
files generated
  dist/sw.js
  dist/workbox-abeb32eb.js
```

**`npx prettier --check "src/**/*.{ts,tsx}"`:** all files formatted correctly, exit 0.

**Dist contents:**
```
$ find ui/dist -type f | sort
ui/dist/assets/index-BXvekFWk.css
ui/dist/assets/index-CnNRnVC-.js
ui/dist/icons/apple-touch-icon.png
ui/dist/icons/icon-192-maskable.png
ui/dist/icons/icon-192.png
ui/dist/icons/icon-512-maskable.png
ui/dist/icons/icon-512.png
ui/dist/index.html
ui/dist/manifest.webmanifest
ui/dist/registerSW.js
ui/dist/sw.js
ui/dist/workbox-abeb32eb.js
```
The icon-generator Python script is NOT in dist/ (lives in `ui/scripts/`, not `ui/public/`).

**Generated `dist/index.html`** contains the manifest link, theme-color, three Apple meta tags, apple-touch-icon, and the auto-injected PWA registration script:
```
<link rel="manifest" href="/manifest.webmanifest" />
<meta name="theme-color" content="#0f0f0f" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
<meta name="apple-mobile-web-app-title" content="Flowstate" />
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png" />
<link rel="icon" type="image/png" sizes="192x192" href="/icons/icon-192.png" />
<link rel="icon" type="image/png" sizes="512x512" href="/icons/icon-512.png" />
...
<script id="vite-plugin-pwa:register-sw" src="/registerSW.js"></script>
```

**Generated `dist/sw.js`** precaches `index.html`, `assets/*.{js,css}`, `manifest.webmanifest`, and all five icons (10 entries / 740.57 KiB), and registers `NetworkOnly` for `/^\/api\//`, `/^\/ws/`, `/^\/health/` plus a `NavigationRoute` with a denylist for the same three patterns.

### Wheel Verification

```
$ uv build --wheel
...
Building UI bundle (npm run build)...
PWA v1.2.0
mode      generateSW
precache  10 entries (740.57 KiB)
files generated
  dist/sw.js
  dist/workbox-abeb32eb.js
Copied UI bundle to /Users/theophanerupin/code/flowstate/src/flowstate/_ui_dist
Successfully built dist/flowstate-0.1.0-py3-none-any.whl

$ unzip -l dist/flowstate-0.1.0-py3-none-any.whl | grep -E "manifest|sw|icons"
      694  flowstate/_ui_dist/manifest.webmanifest
     1674  flowstate/_ui_dist/sw.js
     1263  flowstate/_ui_dist/icons/apple-touch-icon.png
      685  flowstate/_ui_dist/icons/icon-192-maskable.png
      752  flowstate/_ui_dist/icons/icon-192.png
     2308  flowstate/_ui_dist/icons/icon-512-maskable.png
     2311  flowstate/_ui_dist/icons/icon-512.png
```
(The unrelated `flowstate/engine/lumon_plugin/manifest.lumon` entry is filtered out of this transcript.)

### Server Smoke Test (existing dev server, post-build)

The user's `flowstate server` was already running and was serving from the freshly built `ui/dist/` (the `_ui_dist` package data was just rebuilt; static assets are read on every request, only `index.html`'s body is cached at startup):

```
$ curl -i http://127.0.0.1:9090/manifest.webmanifest
HTTP/1.1 200 OK
content-type: application/manifest+json
content-length: 694
{ "name": "Flowstate", "short_name": "Flowstate", ... "display": "standalone", "theme_color": "#0f0f0f", ... }

$ curl -i http://127.0.0.1:9090/sw.js
HTTP/1.1 200 OK
content-type: text/javascript; charset=utf-8
content-length: 1674
[workbox SW with NetworkOnly handlers for /api/, /ws, /health]

$ curl -o /dev/null -w "%{http_code} %{content_type} %{size_download}\n" http://127.0.0.1:9090/icons/icon-192.png
200 image/png 752

$ curl -o /dev/null -w "%{http_code} %{content_type} %{size_download}\n" http://127.0.0.1:9090/icons/apple-touch-icon.png
200 image/png 1263
```

**Note on `/` index.html caching**: the running server's `serve_ui` function reads `index.html` once at startup into `index_content`, so a server already running before this change still returns the pre-PWA HTML on `/`. After the next server restart it will serve the new `index.html` (already verified — the file on disk in both `ui/dist/index.html` and the wheel's `_ui_dist/index.html` contains all the PWA tags). This caching behavior is pre-existing (SERVER-032) and out of scope for this issue.

### Manual Install Verification

The interactive Chrome / Safari install flows (address-bar install icon, "Add to Dock", standalone-window launch from the dock, offline app-shell rendering) are **out of scope for this agent** per the orchestrator's instructions — the agent has no browser surface. All build-time prerequisites for the install flow are present and verified above:

- `manifest.webmanifest` is reachable, has the correct MIME type, declares `display: standalone`, and references all four required icons.
- Both regular and maskable icons exist at 192 + 512 (Android requirement).
- An Apple touch icon exists at 180x180 (iOS Safari "Add to Home/Dock" requirement).
- A registered service worker (`/sw.js`) precaches the app shell and refuses to cache `/api/*`, `/ws`, or `/health`.
- The HTML head exposes the manifest link, theme color, and Apple-specific meta tags.

The user will perform the manual Chrome / Safari install verification.

### Acceptance Criteria Status

- [x] `ui/public/manifest.webmanifest` declares the PWA with all required fields.
- [x] `ui/public/icons/` contains regular + maskable icons at 192/512 plus an apple-touch-icon.
- [x] `ui/index.html` references the manifest and adds Apple meta tags.
- [x] Service worker registers via `vite-plugin-pwa`, precaches the app shell, never caches `/api/*` / `/ws` / `/health`.
- [ ] Chrome "Install Flowstate" prompt — **manual verification deferred to the user** (per orchestrator instruction).
- [ ] Standalone-window title shows "Flowstate" — **manual verification deferred to the user**.
- [x] `npm run build` succeeds; manifest + SW + icons land in `ui/dist/` and in the wheel's `_ui_dist/`.
- [x] No regression on the existing browser experience — `/` still returns `index.html` with the React mount point unchanged.

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
