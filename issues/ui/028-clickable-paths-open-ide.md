# [UI-028] Clickable paths that open in IDE + settings panel for IDE selection

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: UI-022
- Blocks: —

## Summary
All file/directory paths displayed in the UI (workspace, cwd, task_dir, worktree path) should be clickable. Clicking a path opens it in the user's preferred IDE. A settings panel in the UI lets the user choose which IDE to use (VS Code, Cursor, Zed, terminal, etc.). The server provides an API endpoint that executes the IDE open command on the host machine.

## Acceptance Criteria
- [ ] All path displays in the UI are clickable (node details cwd, task_dir, worktree; flow detail workspace)
- [ ] Clicking a path opens it in the configured IDE
- [ ] A settings panel is accessible from the UI (gear icon in sidebar or header)
- [ ] Settings panel lets user select IDE from a preset list (VS Code, Cursor, Zed, Sublime, Terminal)
- [ ] Settings panel allows a custom command template (e.g., `myide {path}`)
- [ ] IDE preference persists across sessions (localStorage or server-side config)
- [ ] Default IDE is VS Code (`code {path}`)

## Technical Design

### Files to Create/Modify

**Server:**
- `src/flowstate/server/routes.py` — add `POST /api/open` endpoint that runs IDE command
- `src/flowstate/config.py` — add `ide_command: str = "code"` config option

**UI:**
- `ui/src/components/ClickablePath/ClickablePath.tsx` — new reusable component for clickable paths
- `ui/src/components/SettingsPanel/SettingsPanel.tsx` — new settings panel component
- `ui/src/components/SettingsPanel/SettingsPanel.css` — styles
- `ui/src/components/NodePill.tsx` — use ClickablePath for cwd/task_dir
- `ui/src/components/FlowDetailPanel/FlowDetailPanel.tsx` — use ClickablePath for workspace
- `ui/src/components/Sidebar/Sidebar.tsx` — add settings gear icon

### Key Implementation Details

**Server endpoint** — `POST /api/open`:
```python
@router.post("/api/open")
async def open_in_ide(request: Request, body: OpenRequest) -> dict:
    """Open a path in the user's IDE."""
    ide_command = request.app.state.config.ide_command  # e.g., "code"
    path = body.path
    # Validate path exists
    if not Path(path).exists():
        raise HTTPException(404, f"Path not found: {path}")
    # Run IDE command in background (don't block)
    subprocess.Popen([ide_command, path])
    return {"status": "opened", "path": path, "command": ide_command}
```

**ClickablePath component**:
```tsx
interface ClickablePathProps {
  path: string;
  truncate?: number;
}

export function ClickablePath({ path, truncate = 30 }: ClickablePathProps) {
  const handleClick = async () => {
    await api.open(path);  // POST /api/open
  };

  return (
    <span
      className="clickable-path"
      title={path}
      onClick={handleClick}
      role="button"
      tabIndex={0}
    >
      {truncatePath(path, truncate)}
    </span>
  );
}
```

**Settings panel** — stores IDE preference in localStorage:
```tsx
const IDE_PRESETS = [
  { name: 'VS Code', command: 'code' },
  { name: 'Cursor', command: 'cursor' },
  { name: 'Zed', command: 'zed' },
  { name: 'Sublime Text', command: 'subl' },
  { name: 'Terminal', command: 'open' },  // macOS Finder
  { name: 'Custom', command: '' },  // user enters custom command
];
```

The selected IDE command is sent with the `POST /api/open` request (or stored server-side via a `POST /api/settings` endpoint). Simpler approach: send the command with each open request so no server-side state is needed:
```json
POST /api/open
{ "path": "/tmp/workspace", "command": "cursor" }
```

**CSS for clickable paths**:
```css
.clickable-path {
  font-family: var(--font-mono);
  cursor: pointer;
  text-decoration: underline;
  text-decoration-style: dotted;
  text-underline-offset: 2px;
  color: var(--accent);
}
.clickable-path:hover {
  text-decoration-style: solid;
  opacity: 0.9;
}
```

### Edge Cases
- Path doesn't exist (deleted workspace) → show error toast, not crash
- IDE not installed → subprocess fails silently, show error toast
- Custom command with spaces → split properly
- Security: validate path is under allowed directories (no arbitrary command execution)
- Windows vs macOS vs Linux: different default commands

## Testing Strategy
- Click a path in node details, verify IDE opens
- Change IDE in settings, click again, verify new IDE opens
- Test with invalid path, verify error handling
- Verify settings persist after page reload (localStorage)
