import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useFlowWatcher } from '../hooks/useFlowWatcher';
import { GraphView } from '../components/GraphView';
import { ErrorBanner } from '../components/ErrorBanner';
import { StartRunModal } from '../components/StartRunModal';
import { FlowDetailPanel } from '../components/FlowDetailPanel';
import { expandEdges } from '../utils/edges';
import type { DiscoveredFlow } from '../api/types';
import './FlowLibrary.css';

// --- Flow Library page ---

export function FlowLibrary() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedFlowId = searchParams.get('flow');
  const { flows } = useFlowWatcher();
  const [selectedFlow, setSelectedFlow] = useState<DiscoveredFlow | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [showStartModal, setShowStartModal] = useState(false);

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
      <div className="flow-library-content">
        {fetchError && <div className="flow-library-error">{fetchError}</div>}

        {selectedFlow ? (
          <>
            <div className="flow-library-header">
              <h2>{selectedFlow.name}</h2>
              {selectedFlow.is_valid && (
                <button
                  className="start-run-btn"
                  data-testid="start-run-btn"
                  onClick={() => setShowStartModal(true)}
                >
                  Start Run
                </button>
              )}
            </div>

            {!selectedFlow.is_valid && (
              <ErrorBanner errors={selectedFlow.errors} />
            )}

            <FlowDetailPanel flow={selectedFlow} />

            <div className="flow-library-graph">
              <GraphView nodes={graphNodes} edges={graphEdges} readOnly />
            </div>
          </>
        ) : (
          !fetchError && (
            <div className="flow-library-no-selection">
              Select a flow from the sidebar
            </div>
          )
        )}
      </div>

      {showStartModal && selectedFlow && (
        <StartRunModal
          flow={selectedFlow}
          onClose={() => setShowStartModal(false)}
        />
      )}
    </div>
  );
}
