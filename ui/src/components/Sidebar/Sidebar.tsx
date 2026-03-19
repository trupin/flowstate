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

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remainMins = mins % 60;
  if (hours < 24)
    return remainMins > 0 ? `${hours}h ${remainMins}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

export function Sidebar() {
  const navigate = useNavigate();
  const { flows } = useFlowWatcher();
  const [activeRuns, setActiveRuns] = useState<FlowRun[]>([]);
  const [recentRuns, setRecentRuns] = useState<FlowRun[]>([]);
  const [schedules, setSchedules] = useState<FlowSchedule[]>([]);
  const [collapsed, setCollapsed] = useState({
    flows: false,
    runs: false,
    history: false,
    schedules: false,
  });

  // Fetch runs on mount and poll every 3 seconds
  useEffect(() => {
    const fetchRuns = () => {
      api.runs
        .list()
        .then((allRuns) => {
          setActiveRuns(allRuns.filter((r) => r.status === 'running'));
          setRecentRuns(
            allRuns.filter((r) => r.status !== 'running').slice(0, 10),
          );
        })
        .catch(() => {
          // silently ignore fetch errors
        });
    };
    fetchRuns();
    const interval = setInterval(fetchRuns, 3000);
    return () => clearInterval(interval);
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
        title="RECENT RUNS"
        collapsed={collapsed.history}
        onToggle={() => setCollapsed((s) => ({ ...s, history: !s.history }))}
      >
        {recentRuns.length === 0 ? (
          <div className="sidebar-empty">No recent runs</div>
        ) : (
          recentRuns.map((run) => (
            <div
              key={run.id}
              className="sidebar-item"
              data-testid={`sidebar-recent-run-${run.id}`}
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
              <span className="sidebar-item-meta">
                {formatElapsed(run.elapsed_seconds)}
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
