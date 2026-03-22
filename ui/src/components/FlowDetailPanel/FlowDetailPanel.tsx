import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import { ClickablePath } from '../ClickablePath';
import type { DiscoveredFlow, FlowRun, EdgeType } from '../../api/types';
import './FlowDetailPanel.css';

interface FlowDetailPanelProps {
  flow: DiscoveredFlow;
}

function formatBudget(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remainMins = mins % 60;
  if (remainMins > 0) return `${hours}h ${remainMins}m`;
  return `${hours}h`;
}

function formatRelativeTime(iso: string | undefined | null): string {
  if (!iso) return '\u2014';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '\u2014';
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  if (diffMs < 0) return 'just now';

  const diffSecs = Math.floor(diffMs / 1000);
  if (diffSecs < 60) return `${diffSecs}s ago`;
  const diffMins = Math.floor(diffSecs / 60);
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

function formatElapsed(seconds: number | undefined | null): string {
  if (seconds == null || isNaN(seconds)) return '\u2014';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remainMins = mins % 60;
  if (remainMins > 0) return `${hours}h ${remainMins}m`;
  return `${hours}h`;
}

const STATUS_SYMBOLS: Record<string, string> = {
  completed: '\u25CF',
  running: '\u25CF',
  paused: '\u25CB',
  failed: '\u25CF',
  cancelled: '\u25CB',
  budget_exceeded: '\u25CF',
  created: '\u25CB',
};

export function FlowDetailPanel({ flow }: FlowDetailPanelProps) {
  const navigate = useNavigate();
  const [recentRuns, setRecentRuns] = useState<FlowRun[]>([]);
  const [sourceExpanded, setSourceExpanded] = useState(false);
  const fetchingRef = useRef(false);

  // Fetch recent runs for this flow
  useEffect(() => {
    if (fetchingRef.current) return;
    fetchingRef.current = true;

    api.runs
      .list()
      .then((allRuns) => {
        const filtered = allRuns
          .filter((r) => r.flow_name === flow.name)
          .sort(
            (a, b) =>
              new Date(b.created_at).getTime() -
              new Date(a.created_at).getTime(),
          )
          .slice(0, 5);
        setRecentRuns(filtered);
      })
      .catch(() => {
        setRecentRuns([]);
      })
      .finally(() => {
        fetchingRef.current = false;
      });
  }, [flow.name]);

  const ast = flow.ast_json;

  // Node summary
  const nodesByType = {
    entry: [] as string[],
    task: [] as string[],
    exit: [] as string[],
  };
  for (const node of flow.nodes) {
    const bucket = nodesByType[node.type];
    if (bucket) {
      bucket.push(node.name);
    }
  }

  // Edge summary
  const edgeCounts: Record<EdgeType, number> = {
    unconditional: 0,
    conditional: 0,
    fork: 0,
    join: 0,
  };
  for (const edge of flow.edges) {
    edgeCounts[edge.edge_type]++;
  }
  const edgeSummaryParts: string[] = [];
  for (const [type, count] of Object.entries(edgeCounts)) {
    if (count > 0) {
      edgeSummaryParts.push(`${count} ${type}`);
    }
  }

  return (
    <div className="flow-detail-panel">
      {/* Settings section */}
      {ast && (
        <section className="flow-detail-section">
          <h3 className="flow-detail-section-title">Settings</h3>
          <div className="flow-settings-grid">
            <span className="flow-settings-key">Budget</span>
            <span className="flow-settings-value">
              {formatBudget(ast.budget_seconds)}
            </span>

            <span className="flow-settings-key">Context</span>
            <span className="flow-settings-value">{ast.context}</span>

            <span className="flow-settings-key">On Error</span>
            <span className="flow-settings-value">{ast.on_error}</span>

            <span className="flow-settings-key">Workspace</span>
            <span className="flow-settings-value">
              {ast.workspace ? (
                <ClickablePath path={ast.workspace} truncate={30} />
              ) : (
                'not set'
              )}
            </span>

            <span className="flow-settings-key">Judge</span>
            <span className="flow-settings-value">
              {ast.judge ? 'enabled' : 'disabled'}
            </span>

            {ast.schedule && (
              <>
                <span className="flow-settings-key">Schedule</span>
                <span className="flow-settings-value">{ast.schedule}</span>
              </>
            )}

            <span className="flow-settings-key">Skip Permissions</span>
            <span className="flow-settings-value">
              {ast.skip_permissions ? 'yes' : 'no'}
            </span>

            <span className="flow-settings-key">On Overlap</span>
            <span className="flow-settings-value">{ast.on_overlap}</span>
          </div>
        </section>
      )}

      {/* Node summary */}
      <section className="flow-detail-section">
        <h3 className="flow-detail-section-title">
          Nodes ({flow.nodes.length})
        </h3>
        <div className="flow-node-summary">
          {nodesByType.entry.length > 0 && (
            <span className="flow-node-group">
              <span className="flow-node-type-label">entry:</span>{' '}
              {nodesByType.entry.join(', ')}
            </span>
          )}
          {nodesByType.task.length > 0 && (
            <span className="flow-node-group">
              <span className="flow-node-type-label">task:</span>{' '}
              {nodesByType.task.join(', ')}
            </span>
          )}
          {nodesByType.exit.length > 0 && (
            <span className="flow-node-group">
              <span className="flow-node-type-label">exit:</span>{' '}
              {nodesByType.exit.join(', ')}
            </span>
          )}
        </div>
      </section>

      {/* Edge summary */}
      <section className="flow-detail-section">
        <h3 className="flow-detail-section-title">
          Edges ({flow.edges.length})
        </h3>
        <div className="flow-edge-summary">
          {edgeSummaryParts.length > 0 ? (
            <span>{edgeSummaryParts.join(', ')}</span>
          ) : (
            <span className="flow-detail-empty">No edges</span>
          )}
        </div>
      </section>

      {/* Parameters */}
      {flow.params.length > 0 && (
        <section className="flow-detail-section">
          <h3 className="flow-detail-section-title">Parameters</h3>
          <div className="flow-params-list">
            {flow.params.map((param) => (
              <div key={param.name} className="flow-param-item">
                <span className="flow-param-name">{param.name}</span>
                <span className="flow-param-type">({param.type}</span>
                {param.default_value !== undefined &&
                param.default_value !== null ? (
                  <span className="flow-param-default">
                    , default: {String(param.default_value)})
                  </span>
                ) : (
                  <span className="flow-param-type">, required)</span>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Recent runs */}
      <section className="flow-detail-section">
        <h3 className="flow-detail-section-title">Recent Runs</h3>
        <div className="flow-recent-runs">
          {recentRuns.length === 0 ? (
            <span className="flow-detail-empty">No runs yet</span>
          ) : (
            recentRuns.map((run) => (
              <div
                key={run.id}
                className="flow-run-item"
                onClick={() => navigate(`/runs/${run.id}`)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    navigate(`/runs/${run.id}`);
                  }
                }}
              >
                <span className={`flow-run-status-dot status-${run.status}`}>
                  {STATUS_SYMBOLS[run.status] ?? '\u25CB'}
                </span>
                <span className="flow-run-status-label">{run.status}</span>
                <span className="flow-run-time">
                  {formatRelativeTime(run.created_at ?? run.started_at)}
                </span>
                <span className="flow-run-elapsed">
                  ({formatElapsed(run.elapsed_seconds)})
                </span>
              </div>
            ))
          )}
        </div>
      </section>

      {/* DSL Source */}
      {flow.source_dsl && (
        <section className="flow-detail-section">
          <div
            className="flow-source-toggle"
            onClick={() => setSourceExpanded((prev) => !prev)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setSourceExpanded((prev) => !prev);
              }
            }}
            aria-expanded={sourceExpanded}
          >
            <span className="flow-source-chevron">
              {sourceExpanded ? '\u25BE' : '\u25B8'}
            </span>
            <span>Source DSL</span>
          </div>
          {sourceExpanded && (
            <pre className="flow-source-code">
              <code>{flow.source_dsl}</code>
            </pre>
          )}
        </section>
      )}
    </div>
  );
}
