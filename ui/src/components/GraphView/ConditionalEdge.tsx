import { useState, useCallback, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  useOnViewportChange,
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
  const iconRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [popoverPos, setPopoverPos] = useState<{
    top: number;
    left: number;
  } | null>(null);

  // Close popover on viewport pan/zoom
  const handleViewportChange = useCallback(() => {
    setExpanded(false);
  }, []);

  useOnViewportChange({
    onStart: handleViewportChange,
  });

  // Compute popover position from icon bounding rect when expanded
  useEffect(() => {
    if (expanded && iconRef.current) {
      const rect = iconRef.current.getBoundingClientRect();
      setPopoverPos({
        top: rect.top + rect.height / 2,
        left: rect.right + 8,
      });
    } else {
      setPopoverPos(null);
    }
  }, [expanded]);

  // Outside-click handler: check both the icon and portaled popover
  useEffect(() => {
    if (!expanded) return;
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node;
      const clickedOnIcon = iconRef.current?.contains(target) ?? false;
      const clickedInPopover = popoverRef.current?.contains(target) ?? false;
      if (clickedOnIcon) {
        // Let the icon's onClick toggle handle it
        return;
      }
      if (!clickedInPopover) {
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
              ref={iconRef}
              className={iconClass}
              onClick={handleIconClick}
              role="button"
              tabIndex={0}
              title="Show condition"
            >
              if
            </div>
          </div>
        </EdgeLabelRenderer>
      )}
      {expanded &&
        popoverPos &&
        createPortal(
          <div
            ref={popoverRef}
            className="conditional-edge-popover"
            style={{
              position: 'fixed',
              top: popoverPos.top,
              left: popoverPos.left,
              transform: 'translateY(-50%)',
            }}
          >
            {condition}
          </div>,
          document.body,
        )}
    </>
  );
}
