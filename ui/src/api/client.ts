import type {
  AuditEvent,
  ConceptAlternative,
  ExtractedWorkflowFile,
  HorizonEvaluation,
  LLMModelInfo,
  NodeTypeManifest,
  PipelinePreflightReport,
  PipelineRunDetail,
  PipelineRunSummary,
  PipelineStageOutcome,
  PipelineSummary,
  ProposalApproval,
  ProposalRenderRequest,
  ProposalRenderResult,
  ProposalReview,
  RunDetail,
  RunEvent,
  RunSummary,
  WorkflowFileCapabilities,
  WorkflowFileReference,
  WorkflowPreflightReport,
  WorkflowSummary,
} from './types';

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
const API = `${BASE}/api`;

// ---- auth identity (in-memory) ----
// The JWT lives in an HttpOnly cookie set by /auth/token; JS cannot read it.
// We keep _token only for the live session so we can also send the
// Authorization header (belt-and-suspenders with the cookie). _username is
// the auth signal the UI keys off, because after a refresh _token is gone
// but the cookie is still valid and rehydrate() can recover the identity.
let _token: string | null = null;
let _username: string | null = null;

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

// Central fetch wrapper: always send cookies so the HttpOnly auth cookie
// rides along on every request (same-origin via the Vite proxy).
function afetch(input: string, init: RequestInit = {}): Promise<Response> {
  return fetch(input, { ...init, credentials: 'include' });
}

export async function login(username: string, password: string): Promise<{ username: string }> {
  const body = new URLSearchParams({ username, password });
  const r = await afetch(`${BASE}/auth/token`, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!r.ok) throw new Error(`login failed: ${r.status} ${await r.text()}`);
  const data = await r.json();
  _token = data.access_token;
  _username = data.username;
  return { username: data.username };
}

// Recover the session from the HttpOnly cookie after a page refresh, when
// _token is null but the cookie is still valid. Returns the user or null.
export async function rehydrate(): Promise<{ username: string } | null> {
  try {
    const r = await afetch(`${BASE}/auth/me`);
    if (!r.ok) return null;
    const data = await r.json();
    _username = data.username;
    return { username: data.username };
  } catch {
    return null;
  }
}

export async function logout(): Promise<void> {
  try {
    await afetch(`${BASE}/auth/logout`, { method: 'POST' });
  } finally {
    _token = null;
    _username = null;
  }
}

export function isAuthed(): boolean {
  // Cookie-based: presence of a known user is the signal, not the in-memory
  // token (which is HttpOnly-invisible and lost on refresh).
  return _username !== null;
}
export function apiBase(): string {
  return BASE;
}

export function getAuthHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return authHeaders(extra);
}

export function currentUsername(): string | null {
  return _username;
}

export function wsUrl(runId: string, ticket: string): string {
  const wsBase = BASE.replace(/^http/, 'ws');
  return `${wsBase}/api/runs/${runId}/ws?ticket=${encodeURIComponent(ticket)}`;
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
          `${detail.message ?? 'Workflow preflight failed'}${errors ? ` ${errors}` : ''
          }`,
        );
      }
      throw new Error(
        `${r.status} ${typeof detail === 'string'
          ? detail
          : (detail?.message ?? JSON.stringify(detail))
        }`,
      );
    } catch (error) {
      if (error instanceof Error && !error.message.startsWith('Unexpected')) {
        throw error;
      }
      throw new Error(`${r.status} ${text}`, { cause: error });
    }
  }
  return r.json() as Promise<T>;
}

export const api = {
  // ---- node registry
  nodeTypes: () =>
    afetch(`${API}/node-types`, { headers: authHeaders() }).then(j<NodeTypeManifest[]>),
  llmModels: () =>
    afetch(`${API}/llm/models`, { headers: authHeaders() })
      .then(j<{ models: LLMModelInfo[] }>),

  // ---- workflow CRUD
  listWorkflows: () =>
    afetch(`${API}/workflows`, { headers: authHeaders() }).then(j<WorkflowSummary[]>),
  getWorkflow: (name: string) =>
    afetch(`${API}/workflows/by-name/${name}`, { headers: authHeaders() })
      .then(j<{ name: string; yaml: string }>),
  saveWorkflow: (name: string, yaml: string) =>
    afetch(`${API}/workflows/save`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ name, yaml }),
    }).then(j<{ ok: true; name: string }>),
  validateWorkflow: (
    workflow_yaml: string,
    inputs?: Record<string, unknown>,
    check_services = false,
  ) =>
    afetch(`${API}/workflows/validate`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ workflow_yaml, inputs, check_services }),
    }).then(j<WorkflowPreflightReport>),

  workflowFileCapabilities: () =>
    afetch(`${API}/workflow-input-files/capabilities`, {
      headers: authHeaders(),
    }).then(j<WorkflowFileCapabilities>),

  uploadWorkflowFiles: (files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append('files', file);
    return afetch(`${API}/workflow-input-files`, {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    }).then(j<{ files: WorkflowFileReference[] }>);
  },

  extractWorkflowFile: (
    file: WorkflowFileReference,
    max_chars = 1_000_000,
  ) =>
    afetch(`${API}/workflow-input-files/extract`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ file, max_chars }),
    }).then(j<ExtractedWorkflowFile>),

  downloadWorkflowFile: async (ref: WorkflowFileReference) => {
    const params = new URLSearchParams({ key: ref.minio_key });
    const response = await afetch(
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
    afetch(`${API}/workflows/run`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ workflow_yaml, inputs, session_id, run_id }),
    }).then(j<{ run_id: string; status: string; state?: unknown }>),
  resumeWorkflow: (run_id: string, decision: Record<string, unknown>) =>
    afetch(`${API}/workflows/${run_id}/resume`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ decision }),
    }).then(j<{ ok: true }>),
  costForRun: (run_id: string) =>
    afetch(`${API}/cost/run/${run_id}`, { headers: authHeaders() })
      .then(j<{ run_id: string; total_usd: number; by_node: unknown[] }>),
  websocketTicket: (run_id: string) =>
    afetch(`${API}/runs/${run_id}/ws-ticket`, {
      method: 'POST',
      headers: authHeaders(),
    }).then(j<{ ticket: string }>),
  downloadArtifact: async (key: string) => {
    const response = await afetch(api.fileUrl(key, true), { headers: authHeaders() });
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = key.split('/').pop() ?? 'download';
    anchor.click();
    URL.revokeObjectURL(url);
  },
  runHistory: () =>
    afetch(`${API}/runs/mine`, { headers: authHeaders() })
      .then(j<{ count: number; runs: RunSummary[] }>),

  runDetail: (run_id: string) =>
    afetch(`${API}/runs/mine/${run_id}`, { headers: authHeaders() })
      .then(j<{ run: RunDetail; audit: AuditEvent[] }>),
  researchSkills: () =>
    afetch(`${API}/research/skills`, { headers: authHeaders() })
      .then(j<{
        skills: {
          name: string;
          description: string;
          version: string;
          license: string;
        }[];
        load_errors: Record<string, string>;
      }>),
  retryFailedRun: (source_run_id: string, run_id: string) =>
    afetch(`${API}/runs/mine/${source_run_id}/retry`, {
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
  pauseRun: (run_id: string) =>
    afetch(`${API}/runs/mine/${run_id}/pause`, {
      method: 'POST',
      headers: authHeaders(),
    }).then(j<{ run_id: string; pause_requested: boolean; message: string }>),
  resumePausedRun: (run_id: string) =>
    afetch(`${API}/runs/mine/${run_id}/resume`, {
      method: 'POST',
      headers: authHeaders(),
    }).then(j<{
      run_id: string;
      status: string;
      state?: unknown;
      error?: string;
    }>),
  restartRun: (source_run_id: string, run_id: string) =>
    afetch(`${API}/runs/mine/${source_run_id}/restart`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ run_id }),
    }).then(j<{
      run_id: string;
      status: string;
      state?: unknown;
      error?: string;
    }>),
  deleteRun: (run_id: string) =>
    afetch(`${API}/runs/mine/${run_id}`, {
      method: 'DELETE',
      headers: authHeaders(),
    }).then(j<{ run_id: string; deleted: boolean }>),

  // ---- pipelines (chain saved workflows: one's outputs become the next's inputs)
  listPipelines: () =>
    afetch(`${API}/pipelines`, { headers: authHeaders() }).then(j<PipelineSummary[]>),
  getPipeline: (name: string) =>
    afetch(`${API}/pipelines/by-name/${name}`, { headers: authHeaders() })
      .then(j<{ name: string; yaml: string }>),
  savePipeline: (name: string, yaml: string) =>
    afetch(`${API}/pipelines/save`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ name, yaml }),
    }).then(j<{ ok: true; name: string }>),
  validatePipeline: (pipeline_yaml: string, inputs?: Record<string, unknown>) =>
    afetch(`${API}/pipelines/validate`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ pipeline_yaml, inputs }),
    }).then(j<PipelinePreflightReport>),
  runPipeline: (
    pipeline_yaml: string,
    inputs: Record<string, unknown>,
    session_id?: string,
    pipeline_run_id?: string,
  ) =>
    afetch(`${API}/pipelines/run`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ pipeline_yaml, inputs, session_id, pipeline_run_id }),
    }).then(j<PipelineStageOutcome>),
  advancePipeline: (pipeline_run_id: string, session_id?: string) =>
    afetch(`${API}/pipelines/${pipeline_run_id}/advance`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ session_id }),
    }).then(j<PipelineStageOutcome>),
  pipelineRuns: () =>
    afetch(`${API}/pipelines/mine`, { headers: authHeaders() })
      .then(j<{ count: number; runs: PipelineRunSummary[] }>),
  pipelineRunDetail: (pipeline_run_id: string) =>
    afetch(`${API}/pipelines/mine/${pipeline_run_id}`, { headers: authHeaders() })
      .then(j<PipelineRunDetail>),

  proposalReview: (run_id: string) =>
    afetch(`${API}/proposals/runs/${run_id}/review`, {
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
    afetch(
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
    afetch(`${API}/proposals/${proposal_id}/verify-claims`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ graph, model }),
    }).then(j<{
      graph: Record<string, unknown>;
      coverage: ProposalReview['coverage'];
      findings: Record<string, unknown>[];
    }>),

  generateConceptAlternatives: (
    proposal_id: string,
    graph: Record<string, unknown>,
    concept_note = '',
    model = 'claude-opus-5',
  ) =>
    afetch(`${API}/proposals/${proposal_id}/concept-alternatives`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ graph, concept_note, model }),
    }).then(j<{
      alternatives: ConceptAlternative[];
      graph: Record<string, unknown>;
    }>),

  requestProposalApproval: (
    proposal_id: string,
    graph: Record<string, unknown>,
    stage: string,
    selected_concept_id?: string,
  ) =>
    afetch(`${API}/proposals/${proposal_id}/approvals`, {
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
    afetch(
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
    afetch(`${API}/proposals/${proposal_id}/horizon-evaluation`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    }).then(j<HorizonEvaluation>),

  renderProposalPDF: (
    proposal_id: string,
    body: ProposalRenderRequest,
  ) =>
    afetch(`${API}/proposals/${proposal_id}/render`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    }).then(j<ProposalRenderResult>),

  renderProposalDOCX: (
    proposal_id: string,
    body: ProposalRenderRequest & { max_embedded_image_bytes?: number },
  ) =>
    afetch(`${API}/proposals/${proposal_id}/render/docx`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    }).then(j<ProposalRenderResult>),

  goldenSet: (name: string) =>
    afetch(`${API}/eval/golden-set?name=${encodeURIComponent(name)}`, { headers: authHeaders() })
      .then(j<{ name: string; n: number; examples: { id: string; question: string; context: string; reference: string }[] }>),

  runEval: (golden_set: string, judge_model: string) =>
    afetch(`${API}/eval/run`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ golden_set, judge_model }),
    }).then(j<Scorecard>),

  evalHistory: (limit = 20) =>
    afetch(`${API}/eval/history?limit=${limit}`, { headers: authHeaders() })
      .then(j<{ scorecards: Scorecard[] }>),

  scoreOutput: (body: { answer: string; sources: string; question?: string; reference?: string; judge_model?: string }) =>
    afetch(`${API}/eval/score-output`, {
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

type RunEventStreamOptions = {
  signal: AbortSignal;
  lastEventId?: number;
  onOpen: () => void;
  onEvent: (event: RunEvent) => void;
};

export async function streamRunEvents(
  runId: string,
  options: RunEventStreamOptions,
): Promise<{ lastEventId?: number; terminal: boolean }> {
  const headers = authHeaders({
    Accept: 'text/event-stream',
    'Cache-Control': 'no-cache',
  });
  if (options.lastEventId !== undefined) {
    headers['Last-Event-ID'] = String(options.lastEventId);
  }

  const response = await afetch(`${API}/runs/${runId}/events`, {
    method: 'GET',
    headers,
    signal: options.signal,
  });
  if (!response.ok) {
    throw new Error(
      `SSE connection failed: ${response.status} ${await response.text()}`,
    );
  }
  if (!response.body) {
    throw new Error('SSE response has no readable stream');
  }

  options.onOpen();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let lastEventId = options.lastEventId;
  let terminal = false;

  while (!terminal) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    buffer = buffer.replace(/\r\n/g, '\n');

    let boundary = buffer.indexOf('\n\n');
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf('\n\n');

      if (!block || block.startsWith(':')) continue;
      let eventName = 'message';
      let id: number | undefined;
      const dataLines: string[] = [];
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith('id:')) {
          const parsed = Number(line.slice(3).trim());
          if (Number.isFinite(parsed)) id = parsed;
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trimStart());
        }
      }
      if (id !== undefined) lastEventId = id;
      if (eventName === 'ready' || dataLines.length === 0) continue;

      const event = JSON.parse(dataLines.join('\n')) as RunEvent;
      options.onEvent(event);
      terminal = (
        event.type === 'run_completed'
        || event.type === 'run_rejected'
        || event.type === 'run_failed'
      );
    }
    if (done) break;
  }

  return { lastEventId, terminal };
}