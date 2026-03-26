import { useState, useEffect, useCallback } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api } from '../../api/client';
import type { RunResults } from '../../api/types';
import './ResultsModal.css';

type TabId = 'diff' | 'files' | 'summaries';

interface ResultsModalProps {
  runId: string;
  onClose: () => void;
}

function statusIcon(status: string): string {
  switch (status) {
    case 'added':
      return '+';
    case 'deleted':
      return '-';
    case 'modified':
      return '~';
    case 'renamed':
      return 'R';
    default:
      return '?';
  }
}

function statusClass(status: string): string {
  switch (status) {
    case 'added':
      return 'results-file-added';
    case 'deleted':
      return 'results-file-deleted';
    case 'modified':
      return 'results-file-modified';
    default:
      return '';
  }
}

export function ResultsModal({ runId, onClose }: ResultsModalProps) {
  const [results, setResults] = useState<RunResults | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>('summaries');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    api.runs
      .getResults(runId)
      .then((data) => {
        if (cancelled) return;
        setResults(data);

        // Pick the best default tab based on available data
        if (data.git_available && data.git_diff) {
          setActiveTab('diff');
        } else if (data.file_changes && data.file_changes.length > 0) {
          setActiveTab('files');
        } else {
          setActiveTab('summaries');
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Failed to load results');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [runId]);

  // Close on Escape key
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

  const hasDiff = results?.git_available && results.git_diff;
  const hasFiles = results?.file_changes && results.file_changes.length > 0;
  const hasSummaries =
    results && Object.keys(results.task_summaries).length > 0;

  return (
    <div className="results-modal-backdrop" onClick={onClose}>
      <div
        className="results-modal-content"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="results-modal-header">
          <h2>Run Results</h2>
          {results?.workspace && (
            <span className="results-workspace">{results.workspace}</span>
          )}
          <button className="results-modal-close-btn" onClick={onClose}>
            &times;
          </button>
        </div>

        {loading && (
          <div className="results-modal-loading">Loading results...</div>
        )}

        {error && <div className="results-modal-error">{error}</div>}

        {results && !loading && (
          <>
            <div className="results-tabs">
              {hasDiff && (
                <button
                  className={`results-tab ${activeTab === 'diff' ? 'active' : ''}`}
                  onClick={() => setActiveTab('diff')}
                >
                  Diff
                </button>
              )}
              {hasFiles && (
                <button
                  className={`results-tab ${activeTab === 'files' ? 'active' : ''}`}
                  onClick={() => setActiveTab('files')}
                >
                  Files ({results.file_changes?.length})
                </button>
              )}
              {hasSummaries && (
                <button
                  className={`results-tab ${activeTab === 'summaries' ? 'active' : ''}`}
                  onClick={() => setActiveTab('summaries')}
                >
                  Summaries
                </button>
              )}
            </div>

            <div className="results-modal-body">
              {activeTab === 'diff' && hasDiff && (
                <pre className="results-diff">
                  {renderDiff(results.git_diff ?? '')}
                </pre>
              )}

              {activeTab === 'files' && hasFiles && (
                <div className="results-file-list">
                  {results.file_changes?.map((file) => (
                    <div key={file.path} className="results-file-item">
                      <span
                        className={`results-file-status ${statusClass(file.status)}`}
                      >
                        {statusIcon(file.status)}
                      </span>
                      <span className="results-file-path">{file.path}</span>
                    </div>
                  ))}
                </div>
              )}

              {activeTab === 'summaries' && hasSummaries && (
                <div className="results-summaries">
                  {Object.entries(results.task_summaries).map(
                    ([nodeName, summary]) => (
                      <div key={nodeName} className="results-summary-section">
                        <h3 className="results-summary-node">{nodeName}</h3>
                        <div className="results-summary-content">
                          <Markdown remarkPlugins={[remarkGfm]}>
                            {summary}
                          </Markdown>
                        </div>
                      </div>
                    ),
                  )}
                </div>
              )}

              {!hasDiff && !hasFiles && !hasSummaries && (
                <div className="results-modal-empty">
                  No results available for this run.
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/**
 * Render a unified diff with syntax coloring for added/removed lines.
 * Returns an array of React elements with appropriate CSS classes.
 */
function renderDiff(diff: string): React.ReactNode[] {
  return diff.split('\n').map((line, i) => {
    let className = 'diff-line';
    if (line.startsWith('+')) {
      className += ' diff-added';
    } else if (line.startsWith('-')) {
      className += ' diff-removed';
    } else if (line.startsWith('@@')) {
      className += ' diff-hunk';
    } else if (line.startsWith('diff ') || line.startsWith('index ')) {
      className += ' diff-meta';
    }
    return (
      <span key={i} className={className}>
        {line}
        {'\n'}
      </span>
    );
  });
}
