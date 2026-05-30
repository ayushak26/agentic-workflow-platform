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
  | { type: 'node_paused';   run_id: string; node_id: string; context: Record<string, unknown>; ts: string }
  | { type: 'run_completed'; run_id: string; ts: string }
  | { type: 'run_failed';    run_id: string; node_id?: string; error: string; ts: string };

export type RunSnapshot = {
  run_id: string;
  status: 'running' | 'paused' | 'completed' | 'failed';
  node_states: Record<string, 'pending' | 'active' | 'done' | 'paused' | 'failed'>;
};