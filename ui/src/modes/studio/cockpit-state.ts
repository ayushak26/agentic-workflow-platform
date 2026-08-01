import type { RunEvent } from '../../api/types';

// 'pending' is the internal value for the user-facing "Waiting" status —
// kept as-is (not renamed) so it doesn't ripple into every place that keys
// off it (CockpitNode's STATUS_STYLES, NODE_RUN_STATUS_MAP, etc); only the
// label shown to the user says "Waiting". 'skipped' and 'cancelled' are
// derived after the fact by computeReachability/applyCancellation below —
// SSE events and liveRunNodeStatus never produce them directly.
export type NodeStatus =
  | 'pending'
  | 'active'
  | 'done'
  | 'reused'
  | 'paused'
  | 'failed'
  | 'skipped'
  | 'cancelled';
export type RunStatus = 'connecting' | 'running' | 'paused' | 'completed' | 'rejected' | 'failed';

export type CockpitState = {
  runStatus: RunStatus;
  nodeStates: Record<string, NodeStatus>;
  outputPreviews: Record<string, string>;
  pausedNode: { id: string; context: unknown } | null;
  errorMessage: string | null;
};

export function initialCockpitState(nodeIds: string[], streamOpen: boolean): CockpitState {
  const nodeStates: Record<string, NodeStatus> = {};
  for (const id of nodeIds) nodeStates[id] = 'pending';
  return {
    runStatus: streamOpen ? 'running' : 'connecting',
    nodeStates,
    outputPreviews: {},
    pausedNode: null,
    errorMessage: null,
  };
}

export function deriveCockpitState(
  nodeIds: string[],
  events: RunEvent[],
  streamOpen: boolean,
): CockpitState {
  const s = initialCockpitState(nodeIds, streamOpen);
  for (const e of events) {
    switch (e.type) {
      case 'node_started':
        if (e.node_id) s.nodeStates[e.node_id] = 'active';
        break;
      case 'node_completed':
        if (e.node_id) {
          s.nodeStates[e.node_id] = 'done';
          s.outputPreviews[e.node_id] = e.output_preview;
        }
        break;
      case 'node_reused':
        if (e.node_id) {
          s.nodeStates[e.node_id] = 'reused';
          s.outputPreviews[e.node_id] = e.output_preview;
        }
        break;
      case 'node_paused':
        if (e.node_id) {
          s.nodeStates[e.node_id] = 'paused';
          s.pausedNode = { id: e.node_id, context: e.context };
        }
        s.runStatus = 'paused';
        break;
      case 'run_completed':
        s.runStatus = 'completed';
        break;
      case 'run_rejected':
        s.runStatus = 'rejected';
        s.errorMessage = e.error ?? null;
        break;
      case 'run_failed':
        s.runStatus = 'failed';
        s.errorMessage = e.error;
        if (e.node_id) s.nodeStates[e.node_id] = 'failed';
        break;
    }
  }
  return s;
}

// A minimal graph shape both the React Flow nodes/edges arrays already
// satisfy — kept structural rather than importing RFNode/RFEdge so this
// module has no ReactFlow dependency.
export type GraphNode = { id: string };
export type GraphEdge = { source: string; target: string; label?: string };

const TERMINAL_DEAD_END: ReadonlySet<NodeStatus> = new Set(['failed', 'skipped', 'cancelled']);
const DECIDED: ReadonlySet<NodeStatus> = new Set(['done', 'reused']);

/**
 * Which conditional-router route (edge label) a completed node's output
 * chose. Routers report this as `output.route` (see the `_router` closure
 * in app/runtime/compiler.py), matched against the edge's `branches` keys.
 */
function chosenRoute(output: unknown): string | null {
  if (output && typeof output === 'object' && 'route' in (output as Record<string, unknown>)) {
    const route = (output as Record<string, unknown>).route;
    return typeof route === 'string' ? route : null;
  }
  return null;
}

/**
 * True once an edge can never be traversed at runtime — either its source
 * is itself a dead end (failed/skipped/cancelled), or it's one labeled
 * branch of an already-decided router whose chosen route doesn't match.
 * An edge from a router that hasn't completed yet is NOT dead — its fate
 * is still undetermined, so downstream nodes stay 'pending' rather than
 * being prematurely marked 'skipped'.
 */
function isEdgeDead(
  predStatus: NodeStatus | undefined,
  predOutput: unknown,
  edgeLabel: string | undefined,
): boolean {
  if (predStatus && TERMINAL_DEAD_END.has(predStatus)) return true;
  if (edgeLabel == null) return false;
  if (!predStatus || !DECIDED.has(predStatus)) return false;
  return chosenRoute(predOutput) !== edgeLabel;
}

/**
 * Marks nodes unreachable from the workflow's entry point(s) as 'skipped' —
 * today this only matters for the untaken branch of a conditional router
 * (no shipped workflow YAML uses `branches` yet, but the parser already
 * supports it, so this keeps the graph honest if/when one does). Runs as a
 * bounded fixed-point over the node list: a node becomes 'skipped' once
 * every one of its incoming edges is dead (see isEdgeDead), which may only
 * become true after an upstream node has itself just been marked skipped.
 * Nodes already in a terminal or in-progress state are left untouched.
 */
export function computeReachability(
  nodes: GraphNode[],
  edges: GraphEdge[],
  nodeStates: Record<string, NodeStatus>,
  nodeOutputs: Record<string, unknown>,
): Record<string, NodeStatus> {
  const result = { ...nodeStates };
  const incoming = new Map<string, GraphEdge[]>();
  for (const edge of edges) {
    const list = incoming.get(edge.target) ?? [];
    list.push(edge);
    incoming.set(edge.target, list);
  }

  // Bounded by node count: in the worst case, a skip determination
  // propagates one hop further per pass along the longest chain.
  for (let pass = 0; pass < nodes.length; pass += 1) {
    let changed = false;
    for (const node of nodes) {
      if (result[node.id] !== 'pending') continue;
      const preds = incoming.get(node.id);
      if (!preds || preds.length === 0) continue; // entry node — never skipped
      const allDead = preds.every((edge) => (
        isEdgeDead(result[edge.source], nodeOutputs[edge.source], edge.label)
      ));
      if (allDead) {
        result[node.id] = 'skipped';
        changed = true;
      }
    }
    if (!changed) break;
  }
  return result;
}

/**
 * Once a run has ended (failed/rejected), any node that never got a chance
 * to run — still 'pending' and not already 'skipped' by a router decision —
 * is relabeled 'cancelled' rather than left looking like it's still waiting
 * on a run that's already over.
 */
export function applyCancellation(
  nodeStates: Record<string, NodeStatus>,
  runEnded: boolean,
): Record<string, NodeStatus> {
  if (!runEnded) return nodeStates;
  const result = { ...nodeStates };
  for (const id of Object.keys(result)) {
    if (result[id] === 'pending') result[id] = 'cancelled';
  }
  return result;
}

export type StatusCounts = {
  total: number;
  completed: number;
  running: number;
  waiting: number;
  paused: number;
  failed: number;
  skipped: number;
  cancelled: number;
};

/** Tally of node statuses for the left overview panel's summary row. */
export function computeStatusCounts(nodeStates: Record<string, NodeStatus>): StatusCounts {
  const counts: StatusCounts = {
    total: 0, completed: 0, running: 0, waiting: 0, paused: 0, failed: 0, skipped: 0, cancelled: 0,
  };
  for (const status of Object.values(nodeStates)) {
    counts.total += 1;
    switch (status) {
      case 'done':
      case 'reused':
        counts.completed += 1;
        break;
      case 'active':
        counts.running += 1;
        break;
      case 'pending':
        counts.waiting += 1;
        break;
      case 'paused':
        counts.paused += 1;
        break;
      case 'failed':
        counts.failed += 1;
        break;
      case 'skipped':
        counts.skipped += 1;
        break;
      case 'cancelled':
        counts.cancelled += 1;
        break;
    }
  }
  return counts;
}

/** Display label for a NodeStatus — the only place "pending" becomes "Waiting". */
export const STATUS_LABEL: Record<NodeStatus, string> = {
  pending: 'Waiting',
  active: 'Running',
  done: 'Completed',
  reused: 'Completed',
  paused: 'Paused',
  failed: 'Failed',
  skipped: 'Skipped',
  cancelled: 'Cancelled',
};

/**
 * Breadth-first traversal both upstream and downstream of `selectedId` over
 * a plain directed edge list, used to highlight the execution path
 * connected to the selected node and fade everything else.
 */
export function computePathHighlight(
  selectedId: string | null,
  edges: GraphEdge[],
): Set<string> {
  const highlighted = new Set<string>();
  if (!selectedId) return highlighted;
  highlighted.add(selectedId);

  const downstream = new Map<string, string[]>();
  const upstream = new Map<string, string[]>();
  for (const edge of edges) {
    (downstream.get(edge.source) ?? downstream.set(edge.source, []).get(edge.source)!).push(edge.target);
    (upstream.get(edge.target) ?? upstream.set(edge.target, []).get(edge.target)!).push(edge.source);
  }

  function walk(start: string, adjacency: Map<string, string[]>) {
    const queue = [start];
    while (queue.length > 0) {
      const current = queue.shift()!;
      for (const next of adjacency.get(current) ?? []) {
        if (!highlighted.has(next)) {
          highlighted.add(next);
          queue.push(next);
        }
      }
    }
  }
  walk(selectedId, downstream);
  walk(selectedId, upstream);
  return highlighted;
}
