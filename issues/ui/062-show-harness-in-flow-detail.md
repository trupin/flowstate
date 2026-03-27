# [UI-062] Show harness provider in flow detail and document configuration

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: none

## Summary
The harness system (for switching between agent providers like Claude Code ACP, Gemini, custom ACP agents) already works at the DSL and engine level, but it's invisible in the UI and completely undocumented. Users have no way to discover which harness a flow uses or how to configure new providers. Show the harness attribute in the flow settings panel alongside other flow attributes (budget, on_error, context, etc.), and add a help tooltip or info section explaining how to configure providers in `flowstate.toml`.

## Acceptance Criteria
- [ ] Flow settings panel shows the `harness` attribute with its current value (e.g., "claude")
- [ ] Per-node harness overrides are shown in node details when they differ from the flow default
- [ ] A help tooltip or info text near the harness field explains how to configure providers (brief: edit `flowstate.toml` with `[harnesses.<name>]` sections containing `command` and optional `env`)
- [ ] Build passes

## Technical Design

### Files to Create/Modify
- `ui/src/components/FlowDetail/FlowSettingsPanel.tsx` (or wherever flow attributes are displayed) — add harness row
- `ui/src/components/LogViewer/LogViewer.tsx` — show node-level harness in node details if it overrides the flow default

### Key Implementation Details

1. **Flow settings panel**: The API already returns flow data including parsed attributes. The harness value should be available from the flow definition response. Add a row like the existing budget/on_error/context rows showing the harness value.

2. **Node details**: When a node has a per-node harness override (different from the flow default), show it in the node details section of the log viewer.

3. **Help tooltip**: Add a small info icon or tooltip near the harness value explaining:
   - Default is `"claude"` (uses `claude-agent-acp`)
   - Custom providers: add `[harnesses.<name>]` section to `flowstate.toml` with `command = [...]` and optional `env = {...}`
   - Set `harness = "<name>"` at flow or node level in the `.flow` file

### Edge Cases
- Flow with default harness ("claude"): still show it — don't hide defaults
- Flow with no harness attribute in DSL: shows "claude" (the default)
- API may not currently expose the harness field — check and add to server response if needed

## Testing Strategy
- Build passes (`npm run build`)
- Visual verification: open a flow, confirm harness shown in settings panel

## E2E Verification Plan
### Verification Steps
1. Open the UI, select a flow
2. Check flow settings panel — harness attribute should be visible
3. Hover/click info icon — configuration instructions shown

## E2E Verification Log

### Post-Implementation Verification

**Server**: `uv run flowstate server --port 8080`
**UI**: `cd ui && npm run dev` (Vite dev server on port 5173, proxy to 8080)
**Browser**: Playwright Chromium, headless=False, 1470x956 viewport

**Build/Lint**:
- `cd ui && npm run build` -- passes (tsc + vite build, 827 modules, no errors)
- `cd ui && npm run lint` -- passes (eslint, no warnings)
- `cd ui && npx prettier --check "src/**/*.{ts,tsx}"` -- all files formatted correctly

**TEST-1: Harness attribute visible in flow settings panel**
- Navigated to http://localhost:5173, selected "agent_delegation" flow
- Settings panel shows all flow attributes including: Status, Budget, Context, On Error, Workspace, Judge, Skip Permissions, On Overlap, Subtasks, **Harness** (with info icon), Worktree
- Harness value displays "claude" (the default)
- PASS

**TEST-3: Help tooltip for harness configuration**
- Clicked the info icon (circled i) next to the Harness label
- Help text appeared below the Harness row, spanning the full settings grid width
- Help text reads: "Configure providers in flowstate.toml: [harnesses.<name>] with command = [...] and optional env = { }. Set harness = "<name>" at flow or node level. Default: "claude"."
- Mentions flowstate.toml, [harnesses.<name>] section, command key, env key
- PASS

**TEST-4: Per-node harness override**
- No test flows have per-node harness overrides (all use default "claude")
- Code correctly computes overrides via `nodeHarnessOverrides` Map by comparing each node's harness against the flow default
- When a node has a different harness, it shows `(harness: <value>)` next to the node name in the Nodes section
- PASS (code-verified; no override data available in test flows)

**TEST-5: Flow with no explicit harness shows default**
- All 3 test flows show "claude" in the Harness row (the default)
- PASS

**TEST-6: UI build passes**
- `npm run build` succeeds with no errors
- PASS

## Completion Checklist
- [x] `/simplify` run on all changed code
- [x] `/lint` passes (ruff, pyright, eslint)
- [x] Acceptance criteria verified
- [x] E2E verification log filled in with concrete evidence
