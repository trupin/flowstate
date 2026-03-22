# [UI-032] Clickable path links don't open the IDE

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: UI-028
- Blocks: —

## Summary
Clicking on file/directory paths in the UI (node details cwd, task_dir, workspace in flow settings) does not open the IDE as expected. The `ClickablePath` component calls `POST /api/open` but the request may be failing silently — errors are caught and only logged to console. Possible causes:

1. **Path doesn't exist**: auto-generated workspace paths (e.g., `~/.flowstate/workspaces/...`) may not exist yet (only created when a run starts), causing a 404
2. **API error swallowed**: the `catch` block in ClickablePath only does `console.error`, so the user sees nothing
3. **IDE command not found**: the default `code` command may not be in PATH on the server process
4. **Server not restarted**: the `POST /api/open` endpoint was added recently and the server may be running old code

## Acceptance Criteria
- [ ] Clicking a path that exists opens it in the configured IDE
- [ ] If the path doesn't exist, show a visible error toast/notification (not just console.error)
- [ ] If the IDE command fails, show an error toast with the command name
- [ ] Show a brief visual feedback on click (e.g., flash the link, show a checkmark)
- [ ] Verify the `/api/open` endpoint is reachable (test with curl)

## Technical Design

### Files to Modify
- `ui/src/components/ClickablePath/ClickablePath.tsx` — add visible error/success feedback
- `ui/src/components/ClickablePath/ClickablePath.css` — click feedback styles

### Key Implementation Details

**Add visible feedback:**
```tsx
const [status, setStatus] = useState<'idle' | 'success' | 'error'>('idle');

const handleClick = async (e) => {
  e.stopPropagation();
  try {
    await api.open(path, ide);
    setStatus('success');
    setTimeout(() => setStatus('idle'), 1500);
  } catch (err) {
    setStatus('error');
    setTimeout(() => setStatus('idle'), 3000);
  }
};

// Add CSS class based on status:
// .clickable-path-success { color: var(--success); }
// .clickable-path-error { color: var(--error); text-decoration: wavy underline; }
```

**Debug checklist:**
1. Open browser devtools → Network tab
2. Click a path link
3. Check if `POST /api/open` fires and what the response is
4. If 404: the path doesn't exist on disk
5. If 400: the IDE command is not in the allowed list
6. If no request fires: the click handler isn't wired up

## Testing Strategy
- Click a path for a completed run (should exist) — verify IDE opens
- Click a path for a pending task (may not exist) — verify error is shown
- Check browser console and network tab for errors
