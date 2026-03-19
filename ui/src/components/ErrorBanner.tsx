import { useState, useEffect, useRef } from 'react';
import type { FlowError } from '../api/types';
import './ErrorBanner.css';

export interface ErrorBannerProps {
  errors: FlowError[];
}

export function ErrorBanner({ errors }: ErrorBannerProps) {
  const [dismissed, setDismissed] = useState(false);
  const prevErrorsRef = useRef(errors);

  // Reappear when errors change (by reference)
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
    <div className="error-banner" role="alert" data-testid="error-banner">
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
            <span className="error-banner-location">
              Line {err.line}:{err.column}
            </span>
            {err.rule && (
              <span className="error-banner-rule">[{err.rule}]</span>
            )}
            <span className="error-banner-message">{err.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
