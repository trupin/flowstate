# [UI-002] Dark Theme + CSS Variables

## Domain
ui

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: UI-001
- Blocks: UI-003, UI-004, UI-005, UI-006, UI-007

## Spec References
- agents/05-ui.md — "Visual Design" section
- specs.md Section 10 — "Dark mode only. Desktop-only layout."

## Summary
Create the global CSS foundation for the Flowstate UI: a dark-only theme using CSS custom properties (variables). This establishes the visual language used by every component — background colors, text colors, border colors, accent/status colors, typography, and basic reset styles. No light theme, no theme toggle. This is an internal developer tool; functional aesthetics over polish.

## Acceptance Criteria
- [ ] `ui/src/index.css` exists with CSS custom properties on `:root`
- [ ] All theme variables from agents/05-ui.md are defined: `--bg-primary`, `--bg-secondary`, `--bg-tertiary`, `--text-primary`, `--text-secondary`, `--border`, `--accent`, `--success`, `--error`, `--warning`
- [ ] Status color variables are defined for all 7 node statuses: `--status-pending`, `--status-waiting`, `--status-running`, `--status-completed`, `--status-failed`, `--status-skipped`, `--status-paused`
- [ ] Global `body` styles set: `background-color: var(--bg-primary)`, `color: var(--text-primary)`, `margin: 0`, `font-family` (system sans-serif)
- [ ] Monospace font variable defined: `--font-mono` (system monospace stack)
- [ ] No light theme or theme toggle exists
- [ ] `ui/src/main.tsx` imports `index.css`
- [ ] The app renders with a dark background and light text when `npm run dev` is run

## Technical Design

### Files to Create/Modify
- `ui/src/index.css` — global styles and CSS custom properties
- `ui/src/main.tsx` — add `import './index.css'` at the top

### Key Implementation Details

#### `index.css`

```css
:root {
    /* Backgrounds */
    --bg-primary: #0f0f0f;
    --bg-secondary: #1a1a1a;
    --bg-tertiary: #252525;

    /* Text */
    --text-primary: #e0e0e0;
    --text-secondary: #888;

    /* Borders */
    --border: #333;

    /* Accent */
    --accent: #3B82F6;

    /* Semantic */
    --success: #22C55E;
    --error: #EF4444;
    --warning: #EAB308;

    /* Node status colors (from specs.md Section 10.4) */
    --status-pending: #9CA3AF;
    --status-waiting: #A855F7;
    --status-running: #3B82F6;
    --status-completed: #22C55E;
    --status-failed: #EF4444;
    --status-skipped: #F97316;
    --status-paused: #EAB308;

    /* Typography */
    --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen,
        Ubuntu, Cantarell, 'Fira Sans', 'Droid Sans', 'Helvetica Neue', sans-serif;
    --font-mono: 'SF Mono', 'Fira Code', 'Fira Mono', 'Roboto Mono',
        'Menlo', 'Consolas', 'DejaVu Sans Mono', monospace;

    /* Sizing */
    --sidebar-width: 240px;
    --control-panel-height: 56px;
}

/* Reset */
*, *::before, *::after {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 0;
    background-color: var(--bg-primary);
    color: var(--text-primary);
    font-family: var(--font-sans);
    font-size: 14px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}

/* Scrollbar styling for dark theme */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}

::-webkit-scrollbar-track {
    background: var(--bg-secondary);
}

::-webkit-scrollbar-thumb {
    background: var(--border);
    border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
    background: var(--text-secondary);
}

/* Base link styles */
a {
    color: var(--accent);
    text-decoration: none;
}

a:hover {
    text-decoration: underline;
}

/* Button reset */
button {
    font-family: inherit;
    font-size: inherit;
    cursor: pointer;
    border: 1px solid var(--border);
    background: var(--bg-tertiary);
    color: var(--text-primary);
    padding: 6px 12px;
    border-radius: 4px;
}

button:hover {
    background: var(--border);
}

button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

/* Utility: monospace text */
.mono {
    font-family: var(--font-mono);
}
```

#### Key design decisions

1. **System font stacks** — no custom font downloads. `--font-sans` uses the OS default sans-serif. `--font-mono` uses the OS default monospace.
2. **Status colors as variables** — allows components (NodePill, GraphView, ControlPanel) to reference status colors consistently without hardcoding hex values.
3. **Layout sizing variables** — `--sidebar-width` and `--control-panel-height` are defined centrally so the Sidebar, RunDetail, and ControlPanel components share the same measurements.
4. **Scrollbar styling** — dark scrollbars matching the theme, using WebKit pseudo-elements (covers Chrome, Edge, Safari). Firefox will use the OS dark scrollbar.
5. **No CSS modules yet** — components will add their own CSS files (plain CSS or CSS modules) in their respective issues. This file provides only the global foundation.

### Edge Cases
- Ensure `index.css` is imported before any component CSS so variables are available everywhere
- Scrollbar styling is WebKit-only; Firefox users get default dark OS scrollbars (acceptable for MVP)
- If a component needs a new color variable, it should be added to `:root` in this file, not defined inline

## Testing Strategy
No automated tests. Visual verification:
1. `cd ui && npm run dev` — page loads with `#0f0f0f` background and `#e0e0e0` text
2. Inspect `:root` in browser DevTools — all CSS variables are defined
3. No white flash on page load (dark background is set immediately)
