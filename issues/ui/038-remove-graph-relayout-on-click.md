# [UI-038] Remove graph relayout on node click (dead code after UI-035)

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: —

## Spec References
- specs.md Section 10.4 — "Graph Visualization"

## Summary
UI-033 added dagre relayout when node dimensions changed — this was needed because nodes expanded on click, changing their size. UI-035 removed node expansion entirely (nodes are always compact pills). The relayout logic is now dead code that runs unnecessary dimension tracking on every ReactFlow node change event. Remove it to simplify GraphView and eliminate wasted CPU work on every node click.

## Acceptance Criteria
- [ ] `handleNodesChange` dimension-tracking logic removed (the function that checks `change.type === 'dimensions'` and stores measured dimensions)
- [ ] `scheduleDagreRelayout` function removed
- [ ] `measuredDimsRef` ref removed
- [ ] `relayoutTimerRef` removed (if only used by the relayout logic)
- [ ] Initial dagre layout on mount is preserved (nodes still position correctly on first render)
- [ ] Container resize fitView is preserved (graph still refits when detail panel opens/closes)
- [ ] Node count fitView is preserved (graph still refits when new nodes appear during execution)
- [ ] `onNodesChange` can be removed from ReactFlow if no other logic depends on it, or simplified to only handle non-dimension changes
- [ ] Clicking nodes does not trigger dagre relayout (verify via console.log or React DevTools)

## Technical Design

### Files to Modify

- `ui/src/components/GraphView/GraphView.tsx` — Remove:
  - `measuredDimsRef` (Map storing measured dimensions)
  - `relayoutTimerRef` (debounce timer for relayout)
  - `scheduleDagreRelayout()` function (rebuilds nodes with measured dims, re-runs dagre, fits view)
  - `handleNodesChange()` function (detects dimension changes, calls scheduleDagreRelayout)
  - `onNodesChange={handleNodesChange}` prop on `<ReactFlow>` (or simplify if other change types are still needed)

  Keep:
  - Initial dagre layout in `useEffect` that runs when `rfNodes` change
  - Container `ResizeObserver` for panel open/close refitting
  - Node count `fitView` effect for new nodes appearing

### Edge Cases
- Generation badge appearing on retry: adds ~20px width. Not worth relayouting for — dagre spacing (nodesep: 80) absorbs this. Acceptable.
- `isSelected` CSS class: only changes border-color and box-shadow — no dimension impact.

## Regression Risks
- Low risk. The removed code only ran in response to dimension changes, which no longer happen. Existing E2E tests that verify graph layout should still pass.

## Testing Strategy
- `cd ui && npm run lint && npm run build`
- Manual: click nodes in a running flow, verify no layout jumps
- E2E tests in `tests/e2e/` that interact with the graph should pass unchanged

## Completion Checklist
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (eslint, prettier, tsc)
- [ ] Acceptance criteria verified
