import type {
  AuditEvent,
  ConceptAlternative,
  ExtractedWorkflowFile,
  HorizonEvaluation,
  NodeTypeManifest,
  ProposalApproval,
  ProposalReview,
  RunDetail,
  RunSummary,
  WorkflowFileCapabilities,
  WorkflowFileReference,
  WorkflowPreflightReport,
  WorkflowSummary,
} from './types';

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
const API = `${BASE}/api`;

// ---- auth token storage (in-memory; survives the SPA session) ----
let _token: string | null = null;

export type CriterionScore = { criterion: string; score: number; reasoning: string };
export type ExampleResult = {
  example_id: string;
  question: string;
  generated_answer: string;
  scores: CriterionScore[];
};
export type Scorecard = {
  workflow_name: string;
  judge_model: string;
  judge_prompt_version: string;
  n_examples: number;
  per_criterion_mean: Record<string, number>;
  overall_mean: number;
  results: ExampleResult[];
  created_at: string;
};

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
  if (!r.ok) {
    const text = await r.text();
    try {
      const payload = JSON.parse(text);
      const detail = payload?.detail ?? payload;
      const report = detail?.preflight as WorkflowPreflightReport | undefined;
      if (report) {
        const errors = report.issues
          .filter(issue => issue.severity === 'error')
          .slice(0, 5)
          .map(issue => `${issue.code}: ${issue.message}`)
          .join(' · ');
        throw new Error(
          `${detail.message ?? 'Workflow preflight failed'}${
            errors ? ` ${errors}` : ''
          }`,
        );
      }
      throw new Error(
        `${r.status} ${
          typeof detail === 'string'
            ? detail
            : (detail?.message ?? JSON.stringify(detail))
        }`,
      );
    } catch (error) {
      if (error instanceof Error && !error.message.startsWith('Unexpected')) {
        throw error;
      }
      throw new Error(`${r.status} ${text}`);
    }
  }
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
  validateWorkflow: (
    workflow_yaml: string,
    inputs?: Record<string, unknown>,
    check_services = false,
  ) =>
    fetch(`${API}/workflows/validate`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ workflow_yaml, inputs, check_services }),
    }).then(j<WorkflowPreflightReport>),

  workflowFileCapabilities: () =>
    fetch(`${API}/workflow-input-files/capabilities`, {
      headers: authHeaders(),
    }).then(j<WorkflowFileCapabilities>),

  uploadWorkflowFiles: (files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append('files', file);
    return fetch(`${API}/workflow-input-files`, {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    }).then(j<{ files: WorkflowFileReference[] }>);
  },

  extractWorkflowFile: (
    file: WorkflowFileReference,
    max_chars = 1_000_000,
  ) =>
    fetch(`${API}/workflow-input-files/extract`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ file, max_chars }),
    }).then(j<ExtractedWorkflowFile>),

  downloadWorkflowFile: async (ref: WorkflowFileReference) => {
    const params = new URLSearchParams({ key: ref.minio_key });
    const response = await fetch(
      `${API}/workflow-input-files/content?${params.toString()}`,
      { headers: authHeaders() },
    );
    if (!response.ok) {
      throw new Error(`${response.status} ${await response.text()}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = ref.name;
    anchor.click();
    URL.revokeObjectURL(url);
  },

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
  runHistory: () =>
    fetch(`${API}/runs/mine`, { headers: authHeaders() })
      .then(j<{ count: number; runs: RunSummary[] }>),

  runDetail: (run_id: string) =>
    fetch(`${API}/runs/mine/${run_id}`, { headers: authHeaders() })
      .then(j<{ run: RunDetail; audit: AuditEvent[] }>),
  retryFailedRun: (source_run_id: string, run_id: string) =>
    fetch(`${API}/runs/mine/${source_run_id}/retry`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ run_id }),
    }).then(j<{
      run_id: string;
      status: string;
      state?: unknown;
      error?: string;
      retry?: {
        source_run_id: string;
        reused_node_count: number;
      };
    }>),

  proposalReview: (run_id: string) =>
    fetch(`${API}/proposals/runs/${run_id}/review`, {
      headers: authHeaders(),
    }).then(j<ProposalReview>),

  registerSourceVersion: (
    proposal_id: string,
    source_id: string,
    body: {
      content: string;
      title: string;
      identifier?: string;
      authority?: string;
      metadata?: Record<string, unknown>;
    },
  ) =>
    fetch(
      `${API}/proposals/${proposal_id}/sources/${source_id}/versions`,
      {
        method: 'POST',
        headers: authHeaders({ 'content-type': 'application/json' }),
        body: JSON.stringify(body),
      },
    ).then(j<Record<string, unknown>>),

  verifyProposalClaims: (
    proposal_id: string,
    graph: Record<string, unknown>,
    model = 'claude-sonnet-4-5',
  ) =>
    fetch(`${API}/proposals/${proposal_id}/verify-claims`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ graph, model }),
    }).then(j<{
      graph: Record<string, any>;
      coverage: ProposalReview['coverage'];
      findings: Record<string, unknown>[];
    }>),

  generateConceptAlternatives: (
    proposal_id: string,
    graph: Record<string, unknown>,
    concept_note = '',
    model = 'claude-opus-5',
  ) =>
    fetch(`${API}/proposals/${proposal_id}/concept-alternatives`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ graph, concept_note, model }),
    }).then(j<{
      alternatives: ConceptAlternative[];
      graph: Record<string, any>;
    }>),

  requestProposalApproval: (
    proposal_id: string,
    graph: Record<string, unknown>,
    stage: string,
    selected_concept_id?: string,
  ) =>
    fetch(`${API}/proposals/${proposal_id}/approvals`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ graph, stage, selected_concept_id }),
    }).then(j<ProposalApproval>),

  decideProposalApproval: (
    proposal_id: string,
    approval_id: string,
    decision: 'approved' | 'rejected' | 'changes_requested',
    comment?: string,
  ) =>
    fetch(
      `${API}/proposals/${proposal_id}/approvals/${approval_id}/decision`,
      {
        method: 'POST',
        headers: authHeaders({ 'content-type': 'application/json' }),
        body: JSON.stringify({ decision, comment }),
      },
    ).then(j<ProposalApproval>),

  evaluateHorizonProposal: (
    proposal_id: string,
    body: {
      graph: Record<string, unknown>;
      proposal_text: string;
      generator_model?: string;
      evaluator_models?: string[];
    },
  ) =>
    fetch(`${API}/proposals/${proposal_id}/horizon-evaluation`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    }).then(j<HorizonEvaluation>),

  goldenSet: (name: string) =>
    fetch(`${API}/eval/golden-set?name=${encodeURIComponent(name)}`, { headers: authHeaders() })
      .then(j<{ name: string; n: number; examples: { id: string; question: string; context: string; reference: string }[] }>),

  runEval: (golden_set: string, judge_model: string) =>
    fetch(`${API}/eval/run`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ golden_set, judge_model }),
    }).then(j<Scorecard>),

  evalHistory: (limit = 20) =>
    fetch(`${API}/eval/history?limit=${limit}`, { headers: authHeaders() })
      .then(j<{ scorecards: Scorecard[] }>),

  scoreOutput: (body: { answer: string; sources: string; question?: string; reference?: string; judge_model?: string }) =>
    fetch(`${API}/eval/score-output`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    }).then(j<{
      scores: { criterion: string; score: number; reasoning: string }[];
      judge_model: string;
      judge_prompt_version: string;
    }>),

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
