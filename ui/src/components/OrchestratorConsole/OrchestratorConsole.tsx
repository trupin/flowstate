import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../../api/client';
import type { OrchestratorInfo, LogEntry } from '../../api/types';
import { LogViewer } from '../LogViewer';
import './OrchestratorConsole.css';

export interface OrchestratorConsoleProps {
  runId: string;
  /** Whether the run is still active (controls log polling). */
  isActive: boolean;
}

export function OrchestratorConsole({
  runId,
  isActive,
}: OrchestratorConsoleProps) {
  const [orchestrators, setOrchestrators] = useState<OrchestratorInfo[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    null,
  );
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [promptExpanded, setPromptExpanded] = useState(false);

  // Track whether we have done initial fetch
  const fetchedRef = useRef(false);

  // Fetch orchestrator list on mount
  useEffect(() => {
    fetchedRef.current = false;
    api.runs.orchestrators(runId).then((result) => {
      setOrchestrators(result);
      if (result.length > 0 && result[0]) {
        setSelectedSessionId(result[0].session_id);
      }
      fetchedRef.current = true;
    });
  }, [runId]);

  // Fetch logs for the selected orchestrator
  const fetchLogs = useCallback(() => {
    if (!selectedSessionId) return;
    api.runs.orchestratorLogs(runId, selectedSessionId).then((result) => {
      setLogs(result);
    });
  }, [runId, selectedSessionId]);

  // Initial log fetch when orchestrator is selected
  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  // Poll for new logs every 3 seconds while the run is active
  useEffect(() => {
    if (!isActive || !selectedSessionId) return;
    const interval = setInterval(fetchLogs, 3000);
    return () => clearInterval(interval);
  }, [isActive, selectedSessionId, fetchLogs]);

  // Reset state when switching orchestrators
  useEffect(() => {
    setLogs([]);
    setPromptExpanded(false);
  }, [selectedSessionId]);

  const selected = orchestrators.find(
    (o) => o.session_id === selectedSessionId,
  );

  if (fetchedRef.current && orchestrators.length === 0) {
    return (
      <div className="orchestrator-console">
        <div className="orchestrator-empty">No orchestrator sessions</div>
      </div>
    );
  }

  function handleSelectChange(e: React.ChangeEvent<HTMLSelectElement>) {
    setSelectedSessionId(e.target.value);
  }

  return (
    <div className="orchestrator-console">
      <div className="orchestrator-header">
        <span className="orchestrator-header-title">Orchestrator</span>
        {orchestrators.length > 1 && (
          <select
            className="orchestrator-selector"
            value={selectedSessionId ?? ''}
            onChange={handleSelectChange}
          >
            {orchestrators.map((o) => (
              <option key={o.session_id} value={o.session_id}>
                {o.key}
              </option>
            ))}
          </select>
        )}
        {selected && (
          <span className="orchestrator-cwd" title={selected.data_dir}>
            {selected.data_dir}
          </span>
        )}
      </div>

      {selected && selected.system_prompt && (
        <div className="orchestrator-system-prompt">
          <div
            className="orchestrator-system-prompt-header"
            onClick={() => setPromptExpanded((prev) => !prev)}
          >
            <span
              className={`orchestrator-system-prompt-chevron ${promptExpanded ? 'expanded' : ''}`}
            >
              {'\u25B6'}
            </span>
            System Prompt
          </div>
          {promptExpanded && (
            <div className="orchestrator-system-prompt-content">
              {selected.system_prompt}
            </div>
          )}
        </div>
      )}

      <div className="orchestrator-logs">
        <LogViewer logs={logs} taskName={selected?.key ?? 'orchestrator'} />
      </div>
    </div>
  );
}
