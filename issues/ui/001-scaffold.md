# [UI-001] Project Scaffold (Vite + React + TypeScript)

## Domain
ui

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: none
- Blocks: UI-002, UI-003, UI-004, UI-005, UI-006, UI-007, UI-008, UI-009

## Spec References
- specs.md Section 10 — "Web Interface"
- agents/05-ui.md — "Files to Create", "Dependencies", "Key Constraints"

## Summary
Bootstrap the `ui/` directory with a Vite + React + TypeScript project. This is the foundation for the entire frontend — every other UI issue depends on it. The scaffold must include the Vite dev server configured to proxy `/api` and `/ws` routes to the FastAPI backend on port 8080, React Router for client-side navigation, and all required npm dependencies. No application logic yet — just the skeleton that builds and runs.

## Acceptance Criteria
- [ ] `ui/package.json` exists with all required dependencies
- [ ] `ui/tsconfig.json` exists with strict TypeScript configuration
- [ ] `ui/vite.config.ts` exists with proxy configuration for `/api` → `http://localhost:8080` and `/ws` → `ws://localhost:8080`
- [ ] `ui/index.html` exists with a root div and script tag pointing to `src/main.tsx`
- [ ] `ui/src/main.tsx` renders the React app into the root div
- [ ] `ui/src/App.tsx` sets up React Router with placeholder routes for `/` (Flow Library) and `/runs/:id` (Run Detail)
- [ ] `cd ui && npm install` completes without errors
- [ ] `cd ui && npm run dev` starts the Vite dev server without errors
- [ ] `cd ui && npm run build` produces output in `ui/dist/` without errors
- [ ] `cd ui && npm run lint` passes (ESLint configured)
- [ ] No CSS framework is included (no Tailwind, no MUI, no Chakra)
- [ ] React Flow v12+ is installed as `@xyflow/react` (not the old `reactflow` package)

## Technical Design

### Files to Create/Modify
- `ui/package.json` — project metadata, scripts, dependencies
- `ui/tsconfig.json` — TypeScript compiler options
- `ui/tsconfig.node.json` — TypeScript config for Vite config file
- `ui/vite.config.ts` — Vite configuration with proxy
- `ui/index.html` — HTML entry point
- `ui/src/main.tsx` — React entry point
- `ui/src/App.tsx` — Router setup with placeholder routes
- `ui/src/vite-env.d.ts` — Vite client type declarations
- `ui/.eslintrc.cjs` or `ui/eslint.config.js` — ESLint configuration

### Key Implementation Details

#### `package.json`

```json
{
  "name": "flowstate-ui",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "lint": "eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0",
    "preview": "vite preview"
  }
}
```

**Runtime dependencies:**
- `react` (^18)
- `react-dom` (^18)
- `react-router-dom` (^6)
- `@xyflow/react` (^12) — React Flow v12+
- `dagre` (^0.8)

**Dev dependencies:**
- `typescript` (^5)
- `vite` (^5)
- `@vitejs/plugin-react` — Vite React plugin
- `@types/react`, `@types/react-dom`
- `@types/dagre`
- `eslint`, `@typescript-eslint/eslint-plugin`, `@typescript-eslint/parser`
- `eslint-plugin-react-hooks`, `eslint-plugin-react-refresh`

#### `vite.config.ts`

```typescript
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true,
      },
    },
  },
});
```

The proxy ensures that during development (`npm run dev`), API calls and WebSocket connections are forwarded to the FastAPI backend running on port 8080.

#### `tsconfig.json`

Use strict mode. Target ES2020+ with ESNext module resolution. Include `src/**/*`. Set `jsx: "react-jsx"`.

#### `App.tsx`

```typescript
import { BrowserRouter, Routes, Route } from 'react-router-dom';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<div>Flow Library (placeholder)</div>} />
        <Route path="/runs/:id" element={<div>Run Detail (placeholder)</div>} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
```

Routes will be replaced with actual page components in UI-010 and UI-011.

#### `main.tsx`

```typescript
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

### Edge Cases
- Ensure `@xyflow/react` is installed, not the deprecated `reactflow` package
- Verify the WebSocket proxy uses `ws: true` in Vite config (HTTP proxy alone won't forward WS)
- The `ui/dist/` directory is the build output — add it to `.gitignore` within the ui directory
- `node_modules/` should also be in `.gitignore`

## Testing Strategy
No automated tests for the scaffold itself. Verification is manual:
1. `cd ui && npm install` — no errors
2. `cd ui && npm run dev` — Vite dev server starts, browser loads without crash
3. `cd ui && npm run build` — `dist/` directory is created with `index.html` and bundled JS
4. `cd ui && npm run lint` — no lint errors
5. Navigate to `/` and `/runs/test-id` — placeholder text renders
