import type { RunEvent } from '../../api/types';

export type NodeStatus = 'pending' | 'active' | 'done' | 'reused' | 'paused' | 'failed';
export type RunStatus = 'connecting' | 'running' | 'paused' | 'completed' | 'failed';

export type CockpitState = {
  runStatus: RunStatus;
  nodeStates: Record<string, NodeStatus>;
  outputPreviews: Record<string, string>;
  pausedNode: { id: string; context: unknown } | null;
  errorMessage: string | null;
};

export function initialCockpitState(nodeIds: string[], wsOpen: boolean): CockpitState {
  const nodeStates: Record<string, NodeStatus> = {};
  for (const id of nodeIds) nodeStates[id] = 'pending';
  return {
    runStatus: wsOpen ? 'running' : 'connecting',
    nodeStates,
    outputPreviews: {},
    pausedNode: null,
    errorMessage: null,
  };
}

export function deriveCockpitState(
  nodeIds: string[],
  events: RunEvent[],
  wsOpen: boolean,
): CockpitState {
  const s = initialCockpitState(nodeIds, wsOpen);
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
      case 'run_failed':
        s.runStatus = 'failed';
        s.errorMessage = e.error;
        if (e.node_id) s.nodeStates[e.node_id] = 'failed';
        break;
    }
  }
  return s;
}
