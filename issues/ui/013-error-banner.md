# [UI-013] Error Banner (file watcher errors)

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: UI-002, UI-008
- Blocks: none

## Spec References
- specs.md Section 10.8 — "File Watcher" (error display behavior)
- agents/05-ui.md — "Error Banner (`ErrorBanner.tsx`)"

## Summary
Create a persistent error banner component that displays parse and type-check errors for `.flow` files. The banner sits above the graph preview area on the Flow Library page and shows detailed error information including line numbers, column numbers, error messages, and rule codes. It is dismissable but reappears when new errors are detected. This is NOT a toast notification — it is a persistent, visible banner that ensures users see errors immediately.

## Acceptance Criteria
- [ ] Banner includes `data-testid="error-banner"` attribute (required for E2E tests)
- [ ] `ui/src/components/ErrorBanner.tsx` exists and renders an error banner
- [ ] `ui/src/components/ErrorBanner.css` exists with banner styles
- [ ] Banner displays a list of errors with line number, column number, and message for each
- [ ] Banner displays rule code (e.g., `S1`, `E3`, `C1`) when available
- [ ] Banner has a dismiss/close button (X icon)
- [ ] After dismissal, banner reappears if new errors arrive (errors prop changes)
- [ ] Banner is visually prominent: red/error-colored border, dark background
- [ ] Banner does NOT auto-dismiss (no timeout)
- [ ] Banner renders correctly with a single error
- [ ] Banner renders correctly with multiple errors
- [ ] Banner is accessible: uses `role="alert"` for screen readers

## Technical Design

### Files to Create/Modify
- `ui/src/components/ErrorBanner.tsx` — error banner component
- `ui/src/components/ErrorBanner.css` — banner styles

### Key Implementation Details

#### Props interface

```typescript
interface ErrorBannerProps {
    errors: FlowError[];
}
```

Where `FlowError` is:
```typescript
interface FlowError {
    line: number;
    column: number;
    message: string;
    rule?: string;    // e.g., "S1", "E3", "C1"
}
```

#### Component structure

```typescript
import { useState, useEffect, useRef } from 'react';
import type { FlowError } from '../api/types';
import './ErrorBanner.css';

export function ErrorBanner({ errors }: ErrorBannerProps) {
    const [dismissed, setDismissed] = useState(false);
    const prevErrorsRef = useRef(errors);

    // Reappear when errors change
    useEffect(() => {
        if (errors !== prevErrorsRef.current && errors.length > 0) {
            setDismissed(false);
        }
        prevErrorsRef.current = errors;
    }, [errors]);

    if (dismissed || errors.length === 0) {
        return null;
    }

    return (
        <div className="error-banner" role="alert">
            <div className="error-banner-header">
                <span className="error-banner-title">
                    {errors.length} error{errors.length > 1 ? 's' : ''} found
                </span>
                <button
                    className="error-banner-dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label="Dismiss errors"
                >
                    &times;
                </button>
            </div>
            <ul className="error-banner-list">
                {errors.map((err, i) => (
                    <li key={i} className="error-banner-item">
                        <span className="error-location">
                            Line {err.line}:{err.column}
                        </span>
                        {err.rule && (
                            <span className="error-rule">[{err.rule}]</span>
                        )}
                        <span className="error-message">{err.message}</span>
                    </li>
                ))}
            </ul>
        </div>
    );
}
```

#### CSS

```css
.error-banner {
    background: rgba(239, 68, 68, 0.08);
    border: 1px solid var(--error);
    border-radius: 6px;
    margin: 8px 12px;
    overflow: hidden;
}

.error-banner-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 12px;
    background: rgba(239, 68, 68, 0.12);
    border-bottom: 1px solid rgba(239, 68, 68, 0.2);
}

.error-banner-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--error);
}

.error-banner-dismiss {
    background: transparent;
    border: none;
    color: var(--error);
    font-size: 18px;
    cursor: pointer;
    padding: 0 4px;
    line-height: 1;
    opacity: 0.7;
}

.error-banner-dismiss:hover {
    opacity: 1;
}

.error-banner-list {
    list-style: none;
    margin: 0;
    padding: 8px 12px;
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.error-banner-item {
    font-size: 12px;
    font-family: var(--font-mono);
    display: flex;
    gap: 8px;
    align-items: baseline;
}

.error-location {
    color: var(--text-secondary);
    flex-shrink: 0;
    min-width: 80px;
}

.error-rule {
    color: var(--error);
    font-weight: 600;
    flex-shrink: 0;
}

.error-message {
    color: var(--text-primary);
}
```

#### Dismiss and reappear logic

The banner tracks the current `errors` prop via a ref. When the `errors` array reference changes (new errors detected, e.g., from a file watcher event), `dismissed` is reset to `false` and the banner reappears. This means:

1. User sees errors → dismisses the banner
2. File is saved with different errors → banner reappears with new errors
3. File is saved with no errors → `errors.length === 0` → banner stays hidden
4. File is saved with the same errors → array reference changes → banner reappears (correct behavior: user should re-acknowledge)

#### Positioning

The banner is a regular flow element, not fixed/absolute. It sits above the graph preview in the FlowLibrary page layout:

```
┌─────────────────────────┐
│  Flow Name    [Start]   │  ← header
├─────────────────────────┤
│  ⚠ 3 errors found    ✕  │  ← ErrorBanner (only if errors)
│  Line 12:5 [S3] ...    │
│  Line 18:1 [E2] ...    │
├─────────────────────────┤
│                         │
│       Graph Preview     │  ← GraphView (may show last valid graph)
│                         │
└─────────────────────────┘
```

### Edge Cases
- Empty errors array — banner renders nothing (`null`)
- Single error — "1 error found" (singular)
- Many errors (10+) — banner scrolls if it exceeds a reasonable height (add `max-height` and `overflow-y: auto` to the list)
- Error without rule code — rule badge is simply not shown
- Error with very long message — text wraps within the banner
- Errors reference changes to the same content — banner reappears (by reference, not deep equality; this is acceptable behavior)
- Component unmounts while dismissed — state is lost; fresh mount starts un-dismissed

## Testing Strategy
Minimal for MVP:
1. Component renders nothing when errors array is empty
2. Component renders banner with error details when errors are provided
3. Component hides after dismiss button is clicked
4. Component reappears when errors prop changes
5. Visual verification: red-themed banner, readable error details, dismiss button works
