# Visual Evaluation: Phase 20 Fixes

**Date**: 2026-03-27
**Run**: 6e0b5e3d-b5f7-44f5-bb43-8061fb15f2dc (discuss_flowstate, completed)
**URL**: http://localhost:9090/runs/6e0b5e3d-b5f7-44f5-bb43-8061fb15f2dc
**Verdict**: PARTIAL (5 of 6 areas pass)

## Test Environment

- Backend: port 9090 (serves built UI + API)
- Viewport: 1920x1080
- Browser: Chromium via Playwright (headless=False)
- Run has: moderator (x3), alice (x2), bob (x2), done (x1); 3 unconditional edges + 1 conditional edge

## Findings

### 1. UI-055: Execution Picker -- PASS

| Criterion | Result | Evidence |
|-----------|--------|----------|
| Tab bar appears for multi-run nodes | PASS | alice shows "Run 1 / Run 2", moderator shows "Run 1 / Run 2 / Run 3", bob shows "Run 1 / Run 2" |
| Defaults to latest execution | PASS | Run 2 active for alice/bob, Run 3 active for moderator on first click |
| Switching tabs changes log content | PASS | alice Run 1 starts at 19:11:36, Run 2 starts at 19:15:13 -- different content confirmed |
| Single-execution node shows no picker | PASS | Done node (1 execution) shows 0 tabs |
| Active tab styling correct | PASS | Active tab has "active" class, inactive tabs do not; switches correctly on click |

All acceptance criteria for UI-055 are met.

### 2. UI-056: Lock Button Removed -- PASS

| Criterion | Result | Evidence |
|-----------|--------|----------|
| Lock button not visible | PASS | No `.react-flow__controls-interactive` element found |
| Only 3 buttons remain | PASS | Exactly 3 buttons: "Zoom In", "Zoom Out", "Fit View" |

All acceptance criteria for UI-056 are met.

### 3. UI-057: Conditional Edge Icons -- FAIL

| Criterion | Result | Evidence |
|-----------|--------|----------|
| Conditional edges show icon instead of text | PASS | "if" icon (44x44) on moderator->done edge. No truncated text labels on any edges. |
| Clicking icon reveals full condition text | PASS | Popover shows: "Alice and Bob have reached consensus on the topic and there are no open disagreements" |
| Clicking elsewhere dismisses popover | PASS | Empty graph, sidebar, node clicks all dismiss the popover |
| Clicking icon again dismisses popover | FAIL | Popover stays visible after second click on icon |
| Unconditional edges unchanged | PASS | 3 unconditional edges have no icons or labels |
| Back edges unchanged | PASS | No back edge labels observed |
| Edge colors, dashing, animation work | PASS | Conditional edge has `stroke-dasharray: 5px, 5px`; unconditional edges solid |

**FAIL-1: Popover does not toggle off when clicking the icon again**
- **Criterion**: "Clicking elsewhere or clicking the icon again dismisses the popover"
- **Expected**: Clicking the "if" icon a second time should close the popover (toggle behavior)
- **Observed**: Popover stays visible after second click. Tested 3 times with consistent result.
- **Steps to reproduce**:
  1. Navigate to http://localhost:9090/runs/6e0b5e3d-b5f7-44f5-bb43-8061fb15f2dc
  2. Click the "if" icon on the conditional edge (between moderator and done)
  3. Popover appears showing the full condition text
  4. Click the "if" icon again
  5. Observe: popover remains visible (should dismiss)

### 4. UI-058/059: Popover Positioning -- PASS

| Criterion | Result | Evidence |
|-----------|--------|----------|
| Popover renders IN FRONT of all nodes | PASS | z-index chain resolves to 10000; visually confirmed in screenshots -- popover overlays graph elements |
| Popover positioned to the right of icon | PASS | Icon at x=922, popover at x=974 (8px gap to the right) |

### 5. ENGINE-055: Generation Badges -- PASS

| Criterion | Result | Evidence |
|-----------|--------|----------|
| Unconditional edge re-entry increments generation | PASS | API confirms: moderator gen 1,2,3; alice gen 1,2; bob gen 1,2 |
| UI shows correct badges | PASS | moderator "x3", alice "x2", bob "x2" visible on graph |
| Non-cyclic nodes still use gen 1 | PASS | done has gen=1 (no badge) |
| Bug is fixed vs prior run | PASS | Run 326c1423 (pre-fix) had moderator all gen=1, bob all gen=1. Run 6e0b5e3d (post-fix) has correct incrementing generations. |

### 6. Other Visual Observations

| Finding | Severity | Notes |
|---------|----------|-------|
| 404 on /api/runs/{id}/orchestrators | Minor | Console error on page load; API endpoint may not exist |
| Vite proxy misconfigured | Info | vite.config.ts points to port 8080 but backend is on 9090; only affects dev server, built UI on port 9090 works fine |
| No Escape key dismiss for popover | Minor | Escape does not dismiss the popover; not required by spec but expected UX convention |

## Screenshots

All saved to /tmp/flowstate-eval-*.png:
- 05: High-res initial load (1920x1080)
- 07: Graph only (cropped) -- shows all 4 nodes, edges, "if" icon, generation badges
- 08: Alice node clicked -- execution picker with Run 1/Run 2
- 09: Alice Run 1 selected
- 10: Alice Run 2 selected
- 11: Moderator clicked -- Run 1/Run 2/Run 3 tabs
- 13: After clicking "if" icon (popover visible)
- 16: Popover retry (visible, positioned right of icon)
- 17: Zoomed popover -- shows "if" icon + condition text popover
- 19: Toggle test -- popover stays after 2nd click (bug)
- 20-22: Moderator Run 3, Run 1, Run 2 switching
- 23: Bob node with 2 tabs
- 24: Done node with no tabs
- 25: Final overview

## Summary

5 of 6 tested areas pass. The only failure is UI-057 criterion 3: the popover does not dismiss when clicking the "if" icon a second time (toggle-off is broken). Clicking elsewhere correctly dismisses the popover. All other features -- execution picker (UI-055), lock button removal (UI-056), popover positioning (UI-058/059), and generation badges (ENGINE-055) -- work correctly and match their specifications.
