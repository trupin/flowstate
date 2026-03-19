# [UI-012] Start Run Modal

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: UI-009, UI-010
- Blocks: none

## Spec References
- specs.md Section 10.9 — "Start Run Modal"
- agents/05-ui.md — "Start Run Modal (`StartRunModal.tsx`)"

## Summary
Create the modal dialog that allows users to start a new flow run with parameters. The modal is triggered by the "Start Run" button on the Flow Library page. It auto-generates a form from the flow's DSL `param` declarations with type-appropriate controls (text input for `string`, number input for `number`, checkbox for `bool`). Default values are pre-filled. Submitting the form calls `POST /api/flows/:id/runs` and navigates to the new run's detail page.

## Acceptance Criteria
- [ ] Modal includes `data-testid="start-run-modal"`, param inputs include `data-testid="param-{name}"`, start button includes `data-testid="start-run-btn"` (required for E2E tests)
- [ ] `ui/src/components/StartRunModal.tsx` exists and renders a modal overlay
- [ ] `ui/src/components/StartRunModal.css` exists with modal styles
- [ ] Modal displays the flow name as the title
- [ ] Modal auto-generates form fields from the flow's `params` array
- [ ] `string` params render as text inputs
- [ ] `number` params render as number inputs
- [ ] `bool` params render as checkboxes
- [ ] Default values are pre-filled from `FlowParam.default_value`
- [ ] Params without defaults start empty (text/number) or unchecked (bool)
- [ ] "Start" button calls `POST /api/flows/:id/runs` with the param values
- [ ] On successful start, navigates to `/runs/:new_run_id`
- [ ] "Cancel" button closes the modal without starting a run
- [ ] Clicking the backdrop (outside the modal) closes it
- [ ] Pressing Escape closes the modal
- [ ] "Start" button is disabled while the request is in-flight
- [ ] Error message displayed if the API call fails
- [ ] Modal renders correctly with zero params (just flow name + Start button)

## Technical Design

### Files to Create/Modify
- `ui/src/components/StartRunModal.tsx` — modal component
- `ui/src/components/StartRunModal.css` — modal styles

### Key Implementation Details

#### Props interface

```typescript
interface StartRunModalProps {
    flow: DiscoveredFlow;
    onClose: () => void;
}
```

#### Component structure

```typescript
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { DiscoveredFlow, FlowParam } from '../api/types';
import './StartRunModal.css';

export function StartRunModal({ flow, onClose }: StartRunModalProps) {
    const navigate = useNavigate();
    const [params, setParams] = useState<Record<string, string | number | boolean>>({});
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Initialize params with defaults
    useEffect(() => {
        const defaults: Record<string, string | number | boolean> = {};
        flow.params.forEach(p => {
            if (p.default_value !== undefined) {
                defaults[p.name] = p.default_value;
            } else {
                // Initialize with type-appropriate empty values
                switch (p.type) {
                    case 'string': defaults[p.name] = ''; break;
                    case 'number': defaults[p.name] = 0; break;
                    case 'bool': defaults[p.name] = false; break;
                }
            }
        });
        setParams(defaults);
    }, [flow.params]);

    // Close on Escape
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose();
        };
        window.addEventListener('keydown', handler);
        return () => window.removeEventListener('keydown', handler);
    }, [onClose]);

    function updateParam(name: string, value: string | number | boolean) {
        setParams(prev => ({ ...prev, [name]: value }));
    }

    async function handleSubmit(e: React.FormEvent) {
        e.preventDefault();
        setSubmitting(true);
        setError(null);

        try {
            const result = await api.runs.start(flow.id, { params });
            navigate(`/runs/${result.id}`);
            onClose();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to start run');
        } finally {
            setSubmitting(false);
        }
    }

    function renderParamField(param: FlowParam) {
        const value = params[param.name];

        switch (param.type) {
            case 'string':
                return (
                    <input
                        type="text"
                        value={(value as string) ?? ''}
                        onChange={e => updateParam(param.name, e.target.value)}
                        placeholder={`Enter ${param.name}`}
                        className="modal-input"
                    />
                );
            case 'number':
                return (
                    <input
                        type="number"
                        value={(value as number) ?? 0}
                        onChange={e => updateParam(param.name, Number(e.target.value))}
                        className="modal-input"
                    />
                );
            case 'bool':
                return (
                    <label className="modal-checkbox">
                        <input
                            type="checkbox"
                            checked={(value as boolean) ?? false}
                            onChange={e => updateParam(param.name, e.target.checked)}
                        />
                        <span>{param.name}</span>
                    </label>
                );
        }
    }

    return (
        <div className="modal-backdrop" onClick={onClose}>
            <div className="modal-content" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true">
                <h2>Start Run: {flow.name}</h2>

                <form onSubmit={handleSubmit}>
                    {flow.params.length > 0 ? (
                        <div className="modal-params">
                            {flow.params.map(param => (
                                <div key={param.name} className="modal-param-field">
                                    <label className="modal-label">
                                        {param.name}
                                        <span className="modal-param-type">{param.type}</span>
                                    </label>
                                    {renderParamField(param)}
                                </div>
                            ))}
                        </div>
                    ) : (
                        <p className="modal-no-params">
                            This flow has no parameters.
                        </p>
                    )}

                    {error && <div className="modal-error">{error}</div>}

                    <div className="modal-actions">
                        <button type="button" onClick={onClose} className="modal-btn-cancel">
                            Cancel
                        </button>
                        <button type="submit" disabled={submitting} className="modal-btn-start">
                            {submitting ? 'Starting...' : 'Start'}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}
```

#### CSS

```css
.modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
}

.modal-content {
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 24px;
    width: 440px;
    max-width: 90vw;
    max-height: 80vh;
    overflow-y: auto;
}

.modal-content h2 {
    margin: 0 0 20px 0;
    font-size: 18px;
}

.modal-params {
    display: flex;
    flex-direction: column;
    gap: 16px;
    margin-bottom: 20px;
}

.modal-param-field {
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.modal-label {
    font-size: 13px;
    font-weight: 500;
    display: flex;
    align-items: center;
    gap: 8px;
}

.modal-param-type {
    font-size: 11px;
    color: var(--text-secondary);
    background: var(--bg-tertiary);
    padding: 1px 6px;
    border-radius: 3px;
    font-family: var(--font-mono);
}

.modal-input {
    background: var(--bg-primary);
    border: 1px solid var(--border);
    color: var(--text-primary);
    padding: 8px 10px;
    border-radius: 4px;
    font-family: inherit;
    font-size: 13px;
}

.modal-input:focus {
    outline: none;
    border-color: var(--accent);
}

.modal-checkbox {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    cursor: pointer;
}

.modal-no-params {
    color: var(--text-secondary);
    font-size: 13px;
    margin: 0 0 20px 0;
}

.modal-error {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid var(--error);
    color: var(--error);
    padding: 8px 12px;
    border-radius: 4px;
    font-size: 13px;
    margin-bottom: 16px;
}

.modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
}

.modal-btn-cancel {
    background: transparent;
    border-color: var(--border);
}

.modal-btn-start {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
    font-weight: 500;
    padding: 8px 20px;
}

.modal-btn-start:disabled {
    opacity: 0.6;
}
```

#### ARIA and keyboard accessibility

- Modal has `role="dialog"` and `aria-modal="true"`
- Escape key closes the modal
- Clicking backdrop closes the modal
- `stopPropagation` on modal content prevents backdrop click handler from firing
- Focus management: for MVP, rely on browser default focus behavior. Enhanced focus trapping can be added in Phase 3.

### Edge Cases
- Flow with zero params — modal shows only the flow name and Start button (no form fields)
- Number param with invalid input (user types "abc") — HTML `type="number"` prevents non-numeric input; `Number()` conversion handles edge cases
- API returns error on start — display error message in the modal, keep modal open
- User double-clicks Start — `submitting` state disables the button after first click
- Modal opened for a flow that becomes invalid while modal is open — unlikely edge case; the Start request will fail and display an error
- Default value is `0` for a number param — should be displayed as `0`, not empty
- Default value is `false` for a bool param — checkbox should be unchecked
- Default value is empty string `""` for a string param — input should be empty

## Testing Strategy
Minimal for MVP:
1. Modal renders without crashing with a flow that has params
2. Modal renders without crashing with a flow that has no params
3. Form fields have correct types (text, number, checkbox)
4. Default values are pre-filled
5. Visual verification: modal appears centered, backdrop darkens, Escape closes
