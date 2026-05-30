import type { NodeTypeManifest, WorkflowSummary, RunSnapshot } from './types';

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
const API = `${BASE}/api`;

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const api = {
  // ---- node registry
  nodeTypes: () => fetch(`${API}/node-types`).then(j<NodeTypeManifest[]>),

  // ---- workflow CRUD
  listWorkflows: () =>
    fetch(`${API}/workflows`).then(j<WorkflowSummary[]>),

  getWorkflow: (name: string) =>
    fetch(`${API}/workflows/by-name/${name}`).then(j<{ name: string; yaml: string }>),

  saveWorkflow: (name: string, yaml: string) =>
    fetch(`${API}/workflows/save`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name, yaml }),
    }).then(j<{ ok: true; name: string }>),

  // ---- execution
  runWorkflow: (workflow_yaml: string, inputs: Record<string, unknown>, session_id?: string, run_id?: string) =>
    fetch(`${API}/workflows/run`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ workflow_yaml, inputs, session_id, run_id }),
    }).then(j<{ run_id: string; status: string; state?: unknown }>),

  resumeWorkflow: (run_id: string, decision: Record<string, unknown>) =>
    fetch(`${API}/workflows/${run_id}/resume`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision }),
    }).then(j<{ ok: true }>),
};

export const wsUrl = (run_id: string) =>
  `${API.replace(/^http/, 'ws')}/ws/runs/${run_id}`;