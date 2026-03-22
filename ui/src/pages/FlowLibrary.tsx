import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useFlowWatcher } from '../hooks/useFlowWatcher';
import { GraphView } from '../components/GraphView';
import { ErrorBanner } from '../components/ErrorBanner';
import { TaskModal } from '../components/TaskModal';
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
  const [showTaskModal, setShowTaskModal] = useState(false);
  const [isEnabled, setIsEnabled] = useState(true);
  const [toggling, setToggling] = useState(false);

  // Fetch the selected flow's full details whenever selectedFlowId or flows change
  useEffect(() => {
    if (selectedFlowId) {
      api.flows
        .get(selectedFlowId)
        .then((flow) => {
          setSelectedFlow(flow);
          setIsEnabled(flow.enabled !== false);
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

  const toggleEnabled = useCallback(async () => {
    if (!selectedFlow || toggling) return;
    setToggling(true);
    try {
      if (isEnabled) {
        await api.flows.disable(selectedFlow.name);
        setIsEnabled(false);
      } else {
        await api.flows.enable(selectedFlow.name);
        setIsEnabled(true);
      }
    } catch {
      // Revert on error — keep current state
    } finally {
      setToggling(false);
    }
  }, [selectedFlow, isEnabled, toggling]);

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
      {fetchError && <div className="flow-library-error">{fetchError}</div>}

      {selectedFlow ? (
        <>
          <div className="flow-library-header">
            <h2>{selectedFlow.name}</h2>
            <div className="flow-controls">
              <button
                className={`flow-toggle ${isEnabled ? 'enabled' : 'disabled'}`}
                onClick={toggleEnabled}
                disabled={toggling}
              >
                {isEnabled ? '\u25CF Enabled' : '\u25CB Disabled'}
              </button>
              {selectedFlow.is_valid && (
                <button
                  className="submit-task-btn"
                  data-testid="submit-task-btn"
                  onClick={() => setShowTaskModal(true)}
                >
                  Submit Task
                </button>
              )}
            </div>
          </div>

          {!selectedFlow.is_valid && (
            <ErrorBanner errors={selectedFlow.errors} />
          )}

          <div className="flow-library-body">
            <div className="flow-library-graph">
              <GraphView nodes={graphNodes} edges={graphEdges} readOnly />
            </div>
            <div className="flow-library-detail-sidebar">
              <FlowDetailPanel flow={selectedFlow} isEnabled={isEnabled} />
            </div>
          </div>
        </>
      ) : (
        !fetchError && (
          <div className="flow-library-no-selection">
            Select a flow from the sidebar
          </div>
        )
      )}

      {showTaskModal && selectedFlow && (
        <TaskModal
          flowName={selectedFlow.name}
          flowParams={selectedFlow.params}
          onClose={() => setShowTaskModal(false)}
          onSubmit={() => setShowTaskModal(false)}
        />
      )}
    </div>
  );
}
