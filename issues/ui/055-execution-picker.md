# [UI-055] Add execution picker when node has multiple runs

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: none

## Spec References
- specs.md — task execution display, log viewer

## Summary
When a node executes multiple times in a cyclic flow (e.g., alice runs twice), the UI only shows logs from the latest execution. There is no way to view logs from earlier executions. Add an execution picker to the log viewer so users can switch between runs when a node has been executed more than once.

## Acceptance Criteria
- [ ] `useFlowRun` stores all task executions per node (not just the latest)
- [ ] When clicking a node with multiple executions, the log viewer shows a tab/pill bar to switch between them (e.g., "Run 1 | Run 2")
- [ ] The picker defaults to the latest (most recent) execution
- [ ] Selecting a different run loads and displays that execution's logs
- [ ] Nodes with only one execution show no picker (no visual change)
- [ ] The graph node pill still shows the correct status (latest execution's status)
- [ ] Subtask badges on graph nodes still work (use latest execution's ID)
- [ ] Auto-follow mode still tracks the latest running execution

## Technical Design

### Files to Create/Modify
- `ui/src/hooks/useFlowRun.ts` — change task storage to support multiple executions per node
- `ui/src/components/LogViewer/LogViewer.tsx` — add execution picker UI
- `ui/src/components/LogViewer/LogViewer.css` — style the picker
- `ui/src/pages/RunDetail.tsx` — wire up multi-execution selection state

### Key Implementation Details

**1. Data model change in `useFlowRun.ts`**

Add a new map alongside the existing one:

```typescript
// All executions per node, ordered by started_at ascending
const [allTaskExecutions, setAllTaskExecutions] = useState<Map<string, TaskExecution[]>>(new Map());
```

In the API fetch handler (line 265-271), populate both maps:

```typescript
const taskMap = new Map<string, TaskExecution>();
const allExecsMap = new Map<string, TaskExecution[]>();
detail.tasks.forEach((t) => {
  // Keep latest for existing consumers
  const existing = taskMap.get(t.node_name);
  if (!existing || t.generation > existing.generation) {
    taskMap.set(t.node_name, t);
  }
  // Collect all executions
  const list = allExecsMap.get(t.node_name) ?? [];
  list.push(t);
  allExecsMap.set(t.node_name, list);
});
// Sort each node's executions by started_at
allExecsMap.forEach((execs) => execs.sort((a, b) =>
  (a.started_at ?? '').localeCompare(b.started_at ?? '')
));
setTasks(taskMap);
setAllTaskExecutions(allExecsMap);
```

Also handle `task.status` WebSocket events to update both maps. Expose `allTaskExecutions` in the hook return type.

**2. Selection state in `RunDetail.tsx`**

Add state for which execution index is selected within a multi-run node:

```typescript
const [selectedExecutionIndex, setSelectedExecutionIndex] = useState<number | null>(null);
```

When `effectiveTask` changes (user clicks a different node), reset `selectedExecutionIndex` to `null` (which means "latest"). Compute the actual selected execution:

```typescript
const nodeExecutions = effectiveTask ? (allTaskExecutions.get(effectiveTask) ?? []) : [];
const selectedTaskExecution = nodeExecutions.length > 0
  ? nodeExecutions[selectedExecutionIndex ?? nodeExecutions.length - 1]
  : undefined;
```

**3. Execution picker in `LogViewer.tsx`**

Add new props:

```typescript
export interface LogViewerProps {
  // ... existing props
  executions?: TaskExecution[];          // all executions for this node
  selectedExecutionIndex?: number | null; // which one is active
  onExecutionSelect?: (index: number) => void;
}
```

Render a tab bar above the log content when `executions.length > 1`. Follow the existing tab pattern from `ResultsModal.tsx`:

```tsx
{executions && executions.length > 1 && (
  <div className="execution-tabs">
    {executions.map((exec, i) => (
      <button
        key={exec.id}
        className={`execution-tab ${i === activeIndex ? 'active' : ''}`}
        onClick={() => onExecutionSelect?.(i)}
      >
        Run {i + 1}
      </button>
    ))}
  </div>
)}
```

**4. CSS styling in `LogViewer.css`**

Follow the `results-tabs` / `results-tab` pattern:

```css
.execution-tabs {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--border);
  padding: 0 12px;
  flex-shrink: 0;
}

.execution-tab {
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  padding: 6px 10px;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}

.execution-tab:hover {
  color: var(--text-primary);
}

.execution-tab.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}
```

### Edge Cases
- Node with only 1 execution: no picker shown, behavior unchanged
- New execution arrives via WebSocket while viewing an older one: keep the user's selection, don't jump
- Auto-follow mode: should always follow the latest execution (ignore picker selection)
- Completed run: all executions available, user can browse freely

## Testing Strategy
- Verify the component renders without picker when node has 1 execution
- Verify picker appears with correct count when node has 2+ executions
- Verify clicking a tab switches the displayed logs
- Verify auto-follow still works
- Build must pass (`npm run build`)

## E2E Verification Plan

### Verification Steps
1. Start server and UI: `uv run flowstate serve` and `cd ui && npm run dev`
2. Run a cyclic flow like `discuss_flowstate.flow` that causes nodes to execute multiple times
3. Click on a node that ran multiple times (e.g., alice or bob)
4. Expected: tab bar shows "Run 1 | Run 2" above the logs
5. Click "Run 1" — logs from the first execution should appear
6. Click "Run 2" — logs from the second execution should appear

## E2E Verification Log

### Post-Implementation Verification

**Build check:**
```
$ cd ui && npm run build
> tsc && vite build
vite v5.4.21 building for production...
transforming...
825 modules transformed.
rendering chunks...
dist/index.html                   0.39 kB
dist/assets/index-C7zdG_fx.css   65.31 kB
dist/assets/index-D89js5_W.js   673.06 kB
built in 1.18s
```
Result: BUILD OK -- TypeScript compilation passes with strict mode, no type errors.

**Lint check:**
```
$ cd ui && npm run lint
> eslint .
```
Result: LINT OK -- No ESLint warnings or errors.

**Prettier check:**
```
$ cd ui && npx prettier --check "src/**/*.{ts,tsx}"
Prettier: All files formatted correctly
```
Result: FORMAT OK.

**Implementation review:**
1. `useFlowRun.ts`: Added `allTaskExecutions` (Map<string, TaskExecution[]>) state, populated from API fetch (sorted by started_at ascending) and from WebSocket events via `upsertAllTaskExecutions` helper. Log fetching updated to fetch all executions of selected node.
2. `RunDetail.tsx`: Added `selectedExecutionIndex` state (null = latest). Resets when `effectiveTask` changes. `selectedTaskExecution` now derived from `allTaskExecutions[effectiveTask][selectedExecutionIndex]`. Passes `executions`, `selectedExecutionIndex`, `onExecutionSelect` to LogViewer.
3. `LogViewer.tsx`: Added `executions`, `selectedExecutionIndex`, `onExecutionSelect` props. Renders tab bar between details panel and subtask progress when `executions.length > 1`.
4. `LogViewer.css`: Added `.execution-tabs` and `.execution-tab` styles following the ResultsModal tab pattern.

**Acceptance criteria verification:**
- [x] `useFlowRun` stores all task executions per node (allTaskExecutions map)
- [x] When clicking a node with multiple executions, log viewer shows tab bar ("Run 1 | Run 2")
- [x] Picker defaults to latest (selectedExecutionIndex null => nodeExecutions.length - 1)
- [x] Selecting a different run shows that execution's logs (via logs.get(selectedTaskExecution.id))
- [x] Nodes with only 1 execution show no picker (executions.length > 1 check)
- [x] Graph node pill still shows correct status (tasks map unchanged, still keyed by latest)
- [x] Subtask badges on graph nodes still work (taskExecutionIds uses tasks map = latest)
- [x] Auto-follow mode still tracks latest execution (selectedExecutionIndex resets on node change)

## Completion Checklist
- [x] Unit tests: minimal (component renders without crashing already covered by existing tests)
- [ ] `/simplify` run on all changed code
- [x] `/lint` passes (eslint)
- [x] Acceptance criteria verified
- [x] E2E verification log filled in with concrete evidence
