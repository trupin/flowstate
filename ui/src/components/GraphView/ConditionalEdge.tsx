import { useState, useCallback, useEffect, useRef } from 'react';
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type EdgeProps,
  type Edge,
} from '@xyflow/react';
import './ConditionalEdge.css';

interface ConditionalEdgeData extends Record<string, unknown> {
  condition?: string;
  stroke?: string;
  isActive?: boolean;
  isTraversed?: boolean;
}

type ConditionalEdgeType = Edge<ConditionalEdgeData, 'conditional'>;

export function ConditionalEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  markerEnd,
  data,
}: EdgeProps<ConditionalEdgeType>) {
  const [expanded, setExpanded] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!expanded) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setExpanded(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [expanded]);

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  const handleIconClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setExpanded((prev) => !prev);
  }, []);

  const condition = data?.condition;
  const isActive = data?.isActive ?? false;
  const isTraversed = data?.isTraversed ?? false;

  let iconClass = 'conditional-edge-icon';
  if (isActive) {
    iconClass += ' conditional-edge-icon--active';
  } else if (isTraversed) {
    iconClass += ' conditional-edge-icon--traversed';
  }

  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style} />
      {condition && (
        <EdgeLabelRenderer>
          <div
            ref={containerRef}
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${String(labelX)}px,${String(labelY)}px)`,
              pointerEvents: 'all',
            }}
          >
            <div
              className={iconClass}
              onClick={handleIconClick}
              role="button"
              tabIndex={0}
              title="Show condition"
            >
              if
            </div>
            {expanded && (
              <div className="conditional-edge-popover">{condition}</div>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
