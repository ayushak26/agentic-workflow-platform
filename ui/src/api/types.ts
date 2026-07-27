// Mirrors app/runtime/schema.py and app/nodes/registry.py manifest output.
export type NodeTypeManifest = {
  type_name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  config_schema: Record<string, unknown>;
};

export type WorkflowSummary = {
  name: string;
  description: string;
  node_count: number;
};

export type RunEvent =
  | { type: 'node_started'; run_id: string; node_id: string; ts: string }
  | { type: 'node_completed'; run_id: string; node_id: string; output_preview: string; ts: string }
  | { type: 'node_reused'; run_id: string; node_id: string; output_preview: string; ts: string }
  | { type: 'node_paused';   run_id: string; node_id: string; context: Record<string, unknown>; ts: string }
  | { type: 'run_completed'; run_id: string; ts: string }
  | { type: 'run_failed';    run_id: string; node_id?: string; error: string; ts: string };

export type RunSnapshot = {
  run_id: string;
  status: 'running' | 'paused' | 'completed' | 'failed';
  node_states: Record<string, 'pending' | 'active' | 'done' | 'reused' | 'paused' | 'failed'>;
};

export type RunStatus =
  | 'running'
  | 'paused'
  | 'completed'
  | 'rejected'
  | 'failed';
 
export type EventType =
  | 'node_start'
  | 'node_end'
  | 'node_reused'
  | 'node_error'
  | 'hitl_approve'
  | 'hitl_reject'
  | 'hitl_edit';
 
export interface RunSummary {
  run_id: string;
  session_id: string;
  workflow_name: string;
  status: RunStatus;
  started_at: number | null;
  ended_at: number | null;
  duration_s: number | null;
  node_count: number | null;
  completed_node_count: number;
  reused_node_count?: number;
  reused_nodes?: string[];
  retry_of_run_id?: string | null;
  attempt?: number;
  active_nodes: string[];
  last_completed_node?: string | null;
  failed_node?: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export type NodeRunStatus =
  | 'running'
  | 'paused'
  | 'completed'
  | 'reused'
  | 'failed';

export interface NodeRun {
  node_id: string;
  type_name: string;
  status: NodeRunStatus;
  input: Record<string, unknown>;
  output: unknown;
  started_at: number | null;
  ended_at: number | null;
  duration_s: number | null;
  error: string | null;
}
 
export interface AuditEvent {
  run_id: string;
  session_id: string;
  node_id: string;
  event_type: EventType;
  actor: string;
  payload: Record<string, unknown>;
  ts: string;
}
export interface RunDetail extends RunSummary {
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  node_runs: Record<string, NodeRun>;
  node_types?: Record<string, string>;
  workflow_yaml?: string;
  retry_available?: boolean;
  retryable_node_count?: number;
}
