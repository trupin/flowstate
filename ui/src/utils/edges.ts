import type { FlowEdgeDef } from '../api/types';

export interface ExpandedEdge {
  source: string;
  target: string;
  edge_type: FlowEdgeDef['edge_type'];
  condition?: string;
}

/**
 * Expand fork/join edge definitions into individual source->target pairs.
 * A fork edge with N targets produces N edges; a join edge with N sources produces N edges.
 */
export function expandEdges(edgeDefs: FlowEdgeDef[]): ExpandedEdge[] {
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
