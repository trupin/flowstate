import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { DiscoveredFlow, FlowParam } from '../api/types';
import './StartRunModal.css';

interface StartRunModalProps {
  flow: DiscoveredFlow;
  onClose: () => void;
}

export function StartRunModal({ flow, onClose }: StartRunModalProps) {
  const navigate = useNavigate();
  const [params, setParams] = useState<
    Record<string, string | number | boolean>
  >({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Initialize params with defaults
  useEffect(() => {
    const defaults: Record<string, string | number | boolean> = {};
    flow.params.forEach((p) => {
      if (p.default_value !== undefined) {
        defaults[p.name] = p.default_value;
      } else {
        switch (p.type) {
          case 'string':
            defaults[p.name] = '';
            break;
          case 'number':
            defaults[p.name] = 0;
            break;
          case 'bool':
            defaults[p.name] = false;
            break;
        }
      }
    });
    setParams(defaults);
  }, [flow.params]);

  // Close on Escape
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    },
    [onClose],
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  function updateParam(name: string, value: string | number | boolean) {
    setParams((prev) => ({ ...prev, [name]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const result = await api.runs.start(flow.id, { params });
      navigate(`/runs/${result.flow_run_id}`);
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
            data-testid={`param-${param.name}`}
            value={(value as string) ?? ''}
            onChange={(e) => updateParam(param.name, e.target.value)}
            placeholder={`Enter ${param.name}`}
            className="modal-input"
          />
        );
      case 'number':
        return (
          <input
            type="number"
            data-testid={`param-${param.name}`}
            value={(value as number) ?? 0}
            onChange={(e) => updateParam(param.name, Number(e.target.value))}
            className="modal-input"
          />
        );
      case 'bool':
        return (
          <label className="modal-checkbox">
            <input
              type="checkbox"
              data-testid={`param-${param.name}`}
              checked={(value as boolean) ?? false}
              onChange={(e) => updateParam(param.name, e.target.checked)}
            />
            <span>{param.name}</span>
          </label>
        );
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-content"
        data-testid="start-run-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <h2>Start Run: {flow.name}</h2>

        <form onSubmit={handleSubmit}>
          {flow.params.length > 0 ? (
            <div className="modal-params">
              {flow.params.map((param) => (
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
            <p className="modal-no-params">This flow has no parameters.</p>
          )}

          {error && <div className="modal-error">{error}</div>}

          <div className="modal-actions">
            <button
              type="button"
              onClick={onClose}
              className="modal-btn-cancel"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="modal-btn-start"
              data-testid="start-run-btn"
            >
              {submitting ? 'Starting...' : 'Start'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
