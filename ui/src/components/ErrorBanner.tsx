import { useState } from 'react';
import type { FlowError } from '../api/types';
import './ErrorBanner.css';

export interface ErrorBannerProps {
  errors: FlowError[];
}

export function ErrorBanner({ errors }: ErrorBannerProps) {
  const [dismissed, setDismissed] = useState(false);

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
            <span className="error-banner-location">
              line {err.line}:{err.column}
            </span>
            <span className="error-banner-message">{err.message}</span>
            {err.rule && (
              <span className="error-banner-rule">[{err.rule}]</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
