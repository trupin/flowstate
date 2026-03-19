import { useState, useEffect, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import { useFlowWatcher } from '../../hooks/useFlowWatcher';
import type { FlowRun, FlowSchedule } from '../../api/types';
import './Sidebar.css';

interface SidebarSectionProps {
  title: string;
  collapsed: boolean;
  onToggle: () => void;
  children: ReactNode;
}

function SidebarSection({
  title,
  collapsed,
  onToggle,
  children,
}: SidebarSectionProps) {
  return (
    <div className="sidebar-section">
      <div
        className="sidebar-section-header"
        onClick={onToggle}
        role="button"
        tabIndex={0}
        aria-expanded={!collapsed}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <span
          className={`collapse-arrow ${collapsed ? 'collapsed' : ''}`}
          aria-hidden="true"
        >
          &#9654;
        </span>
        <span>{title}</span>
      </div>
      {!collapsed && <div className="sidebar-section-content">{children}</div>}
    </div>
  );
}

function formatNextTrigger(iso: string | undefined): string {
  if (!iso) return 'N/A';
  const d = new Date(iso);
  const now = new Date();
  const diffMs = d.getTime() - now.getTime();

  if (diffMs < 0) return 'overdue';

  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 60) return `${diffMins}m`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d`;
}

export function Sidebar() {
  const navigate = useNavigate();
  const { flows } = useFlowWatcher();
  const [activeRuns, setActiveRuns] = useState<FlowRun[]>([]);
  const [schedules, setSchedules] = useState<FlowSchedule[]>([]);
  const [collapsed, setCollapsed] = useState({
    flows: false,
    runs: false,
    schedules: false,
  });

  // Fetch active runs on mount
  useEffect(() => {
    api.runs
      .list('running')
      .then((runs) => {
        setActiveRuns(runs);
      })
      .catch(() => {
        // silently ignore fetch errors
      });
  }, []);

  // Fetch schedules on mount
  useEffect(() => {
    api.schedules
      .list()
      .then((result) => {
        setSchedules(result);
      })
      .catch(() => {
        // silently ignore fetch errors
      });
  }, []);

  return (
    <aside className="sidebar" aria-label="Navigation sidebar">
      <div className="sidebar-brand">FLOWSTATE</div>

      <SidebarSection
        title="FLOWS"
        collapsed={collapsed.flows}
        onToggle={() => setCollapsed((s) => ({ ...s, flows: !s.flows }))}
      >
        {flows.length === 0 ? (
          <div className="sidebar-empty">No flows found</div>
        ) : (
          flows.map((flow) => (
            <div
              key={flow.id}
              className="sidebar-item"
              data-testid={`sidebar-flow-${flow.name}`}
              data-status={flow.is_valid ? 'valid' : 'error'}
              onClick={() => navigate(`/?flow=${flow.id}`)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  navigate(`/?flow=${flow.id}`);
                }
              }}
            >
              <span
                className={`validity-dot ${flow.is_valid ? 'valid' : 'invalid'}`}
                aria-label={flow.is_valid ? 'Valid' : 'Has errors'}
              />
              <span className="sidebar-item-name">{flow.name}</span>
            </div>
          ))
        )}
      </SidebarSection>

      <SidebarSection
        title="ACTIVE RUNS"
        collapsed={collapsed.runs}
        onToggle={() => setCollapsed((s) => ({ ...s, runs: !s.runs }))}
      >
        {activeRuns.length === 0 ? (
          <div className="sidebar-empty">No active runs</div>
        ) : (
          activeRuns.map((run) => (
            <div
              key={run.id}
              className="sidebar-item"
              data-testid={`sidebar-run-${run.id}`}
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
              <span
                className={`status-dot status-${run.status}`}
                aria-label={run.status}
              />
              <span className="sidebar-item-name">
                {run.flow_name} #{run.id.slice(0, 4)}
              </span>
            </div>
          ))
        )}
      </SidebarSection>

      <SidebarSection
        title="SCHEDULES"
        collapsed={collapsed.schedules}
        onToggle={() =>
          setCollapsed((s) => ({
            ...s,
            schedules: !s.schedules,
          }))
        }
      >
        {schedules.length === 0 ? (
          <div className="sidebar-empty">No schedules</div>
        ) : (
          schedules.map((sched) => (
            <div key={sched.id} className="sidebar-item">
              <span className="sidebar-item-name">{sched.flow_name}</span>
              <span className="sidebar-item-meta">
                next: {formatNextTrigger(sched.next_trigger_at)}
              </span>
            </div>
          ))
        )}
      </SidebarSection>
    </aside>
  );
}
