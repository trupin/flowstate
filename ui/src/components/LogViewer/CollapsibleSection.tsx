import { useState } from 'react';
import type { ReactNode } from 'react';

export interface CollapsibleSectionProps {
  label: string;
  children: ReactNode;
  defaultExpanded?: boolean;
}

export function CollapsibleSection({
  label,
  children,
  defaultExpanded = false,
}: CollapsibleSectionProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const handleToggle = () => {
    setExpanded((prev) => !prev);
  };

  return (
    <div className="collapsible-section">
      <div
        className="collapsible-header"
        onClick={handleToggle}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleToggle();
          }
        }}
        aria-expanded={expanded}
      >
        <span className="collapsible-chevron">
          {expanded ? '\u25BE' : '\u25B8'}
        </span>
        <span className="collapsible-label">{label}</span>
      </div>
      {expanded && <div className="collapsible-content">{children}</div>}
    </div>
  );
}
