import type { NodeTypeManifest, WorkflowSummary, RunSnapshot } from './types';

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
const API = `${BASE}/api`;

// ---- auth token storage (in-memory; survives the SPA session) ----
let _token: string | null = null;

export async function login(username: string, password: string): Promise<{ username: string }> {
  const body = new URLSearchParams({ username, password });
  const r = await fetch(`${BASE}/auth/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!r.ok) throw new Error(`login failed: ${r.status} ${await r.text()}`);
  const data = await r.json();
  _token = data.access_token;
  return { username: data.username };
}

export function isAuthed(): boolean {
  return _token !== null;
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return _token ? { ...extra, Authorization: `Bearer ${_token}` } : extra;
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const api = {
  // ---- node registry
  nodeTypes: () =>
    fetch(`${API}/node-types`, { headers: authHeaders() }).then(j<NodeTypeManifest[]>),

  // ---- workflow CRUD
  listWorkflows: () =>
    fetch(`${API}/workflows`, { headers: authHeaders() }).then(j<WorkflowSummary[]>),
  getWorkflow: (name: string) =>
    fetch(`${API}/workflows/by-name/${name}`, { headers: authHeaders() })
      .then(j<{ name: string; yaml: string }>),
  saveWorkflow: (name: string, yaml: string) =>
    fetch(`${API}/workflows/save`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ name, yaml }),
    }).then(j<{ ok: true; name: string }>),

  // ---- execution
  runWorkflow: (workflow_yaml: string, inputs: Record<string, unknown>, session_id?: string, run_id?: string) =>
    fetch(`${API}/workflows/run`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ workflow_yaml, inputs, session_id, run_id }),
    }).then(j<{ run_id: string; status: string; state?: unknown }>),
  resumeWorkflow: (run_id: string, decision: Record<string, unknown>) =>
    fetch(`${API}/workflows/${run_id}/resume`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ decision }),
    }).then(j<{ ok: true }>),
  costForRun: (run_id: string) =>
    fetch(`${API}/cost/run/${run_id}`, { headers: authHeaders() })
      .then(j<{ run_id: string; total_usd: number; by_node: unknown[] }>),

  fileUrl(key: string, download = false): string {
    const params = new URLSearchParams({ key });
    if (download) params.set('download', 'true');
    return `${BASE}/api/files?${params.toString()}`;
  },
};

export const wsUrl = (run_id: string) => {
  const base = BASE.replace(/^http/, "ws");  // http→ws, https→wss
  const t = _token ? `?token=${encodeURIComponent(_token)}` : "";
  return `${base}/api/ws/runs/${run_id}${t}`;
};
