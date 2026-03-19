import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useFlowWatcher } from '../hooks/useFlowWatcher';
import { GraphView } from '../components/GraphView';
import { ErrorBanner } from '../components/ErrorBanner';
import type { DiscoveredFlow, FlowEdgeDef } from '../api/types';
import './FlowLibrary.css';

// --- Edge expansion for fork/join ---

interface ExpandedEdge {
  source: string;
  target: string;
  edge_type: FlowEdgeDef['edge_type'];
  condition?: string;
}

function expandEdges(edgeDefs: FlowEdgeDef[]): ExpandedEdge[] {
  const result: ExpandedEdge[] = [];
  for (const e of edgeDefs) {
    if (e.edge_type === 'fork' && e.source && e.fork_targets) {
      for (const t of e.fork_targets) {
        result.push({ source: e.source, target: t, edge_type: 'fork' });
      }
    } else if (e.edge_type === 'join' && e.target && e.join_sources) {
      for (const s of e.join_sources) {
        result.push({ source: s, target: e.target, edge_type: 'join' });
      }
    } else if (e.source && e.target) {
      result.push({
        source: e.source,
        target: e.target,
        edge_type: e.edge_type,
        condition: e.condition,
      });
    }
  }
  return result;
}

// --- Flow Library page ---

export function FlowLibrary() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedFlowId = searchParams.get('flow');
  const { flows } = useFlowWatcher();
  const [selectedFlow, setSelectedFlow] = useState<DiscoveredFlow | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // Fetch the selected flow's full details whenever selectedFlowId or flows change
  useEffect(() => {
    if (selectedFlowId) {
      api.flows
        .get(selectedFlowId)
        .then((flow) => {
          setSelectedFlow(flow);
          setFetchError(null);
        })
        .catch(() => {
          setSelectedFlow(null);
          setFetchError('Failed to load flow details');
        });
    } else if (flows.length > 0) {
      // Auto-select the first flow if none selected
      const firstFlow = flows[0];
      if (firstFlow) {
        setSearchParams({ flow: firstFlow.id }, { replace: true });
      }
    } else {
      setSelectedFlow(null);
    }
  }, [selectedFlowId, flows, setSearchParams]);

  const handleSelectFlow = useCallback(
    (flowId: string) => {
      setSearchParams({ flow: flowId });
    },
    [setSearchParams],
  );

  // Compute expanded edges for GraphView
  const graphEdges = selectedFlow ? expandEdges(selectedFlow.edges) : [];
  const graphNodes = selectedFlow
    ? selectedFlow.nodes.map((n) => ({
        name: n.name,
        type: n.type,
        prompt: n.prompt,
        cwd: n.cwd,
      }))
    : [];

  return (
    <div className="flow-library">
      <div className="flow-library-list">
        <h2>Flows</h2>
        {flows.length === 0 && (
          <div className="flow-library-empty">
            No flows discovered. Add <code>.flow</code> files to the watched
            directory.
          </div>
        )}
        {flows.map((flow) => (
          <div
            key={flow.id}
            className={`flow-library-item ${flow.id === selectedFlowId ? 'selected' : ''}`}
            onClick={() => handleSelectFlow(flow.id)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                handleSelectFlow(flow.id);
              }
            }}
          >
            <span
              className={`validity-dot ${flow.is_valid ? 'valid' : 'invalid'}`}
              aria-label={flow.is_valid ? 'Valid' : 'Has errors'}
            />
            <div className="flow-library-item-info">
              <span className="flow-library-item-name">{flow.name}</span>
              <span className="flow-library-item-modified">
                {new Date(flow.last_modified).toLocaleString()}
              </span>
            </div>
          </div>
        ))}
      </div>

      <div className="flow-library-preview">
        {fetchError && <div className="flow-library-error">{fetchError}</div>}

        {selectedFlow ? (
          <>
            <div className="flow-library-preview-header">
              <h2>{selectedFlow.name}</h2>
              {selectedFlow.is_valid && (
                <button className="start-run-btn">Start Run</button>
              )}
            </div>

            {!selectedFlow.is_valid && (
              <ErrorBanner errors={selectedFlow.errors} />
            )}

            <div className="flow-library-graph">
              <GraphView nodes={graphNodes} edges={graphEdges} readOnly />
            </div>
          </>
        ) : (
          !fetchError && (
            <div className="flow-library-no-selection">
              Select a flow to preview its graph
            </div>
          )
        )}
      </div>
    </div>
  );
}
