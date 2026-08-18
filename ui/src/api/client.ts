import type {
  AskContext,
  AuditEvent,
  AutofixWorkflowResult,
  BudgetsResponse,
  BusinessRule,
  CacheSummary,
  ConceptAlternative,
  CostOverview,
  DraftCodeRequest,
  DraftInstructionsField,
  CloudFileMeta,
  EmailConnectionInfo,
  FieldSpec,
  InfraAllocationEntry,
  IntegrationConnectionInfo,
  MCPServerInfo,
  MCPToolInfo,
  MCPToolTestResult,
  NodeTestResult,
  OperatorCatalog,
  OutputContract,
  PricingResponse,
  SchemaPreview,
  SimulationResult,
  ExtractedWorkflowFile,
  GenerateWorkflowResult,
  HorizonEvaluation,
  LLMModelInfo,
  NodeTypeManifest,
  OpenRouterModelInfo,
  PipelinePreflightReport,
  PipelineRunDetail,
  PipelineRunSummary,
  PipelineStageOutcome,
  PipelineSummary,
  PendingGate,
  ProposalApproval,
  ProposalRenderRequest,
  ProposalRenderResult,
  ProposalReview,
  BusinessActionResult,
  BusinessExplanation,
  BusinessNarration,
  BusinessProjection,
  BusinessTechnicalDetail,
  RunChatTurn,
  RunCostSummary,
  RunDetail,
  RunEvent,
  RunSummary,
  WorkflowDetail,
  WorkflowDraft,
  WorkflowFileCapabilities,
  WorkflowFileReference,
  WorkflowPreflightReport,
  WorkflowStats,
  WorkflowSummary,
  WorkflowVersionSummary,
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
export type RunCandidate = {
  source_id: string;
  version_id: string;
  key: string;
  size: number;
  last_modified: string | null;
  pdf_url: string;
};
export type DiscoveredCandidate = {
  candidate_id: string;
  claim_id: string | null;
  title: string;
  url: string | null;
  doi: string | null;
  source: string | null;
  purpose: string | null;
  authority: string | null;
  retraction_status: string | null;
  found_by_node_id: string | null;
  found_by_type:
    | 'ScholarlyCandidateDiscoveryAgent'
    | 'BoundedDeepResearchAgent'
    | 'PriorProjectRetrieverAgent'
    | 'StructuredDatasetRetrieverAgent'
    | string;
};
export type ClaimVerificationResult = {
  verified: boolean;
  confidence: 'low' | 'medium' | 'high';
  source_type: 'website' | 'book' | 'citation' | 'unknown';
  source_name: string;
  source_url: string | null;
  citation: string;
  notes: string;
};
export type InternalEvidenceRecord = {
  record_id: string;
  claim_id: string | null;
  fact_key: string | null;
  content: string;
  source_name: string | null;
  source_class: string | null;
  verification_status: string | null;
  drafting_allowed: boolean | null;
  found_by_node_id: string | null;
  verification: ClaimVerificationResult | null;
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

export type WorkflowFieldCheck = {
  field: string;
  expected: unknown;
  actual: unknown;
  passed: boolean;
};
export type WorkflowCaseResult = {
  case_id: string;
  label: string;
  model: string;
  passed: boolean;
  checks: WorkflowFieldCheck[];
  cost_usd: number | null;
  latency_ms: number | null;
  error: string | null;
};
export type ModelComparisonResult = {
  model: string;
  total_cases: number;
  passed_cases: number;
  pass_rate: number;
  avg_cost_usd: number | null;
  avg_latency_ms: number | null;
  cases: WorkflowCaseResult[];
};
export type WorkflowCompareResponse = {
  golden_set: string;
  comparisons: ModelComparisonResult[];
  recommendation: { model: string; reason: string } | null;
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
// Concurrent callers (StrictMode's double-invoked mount effect, multiple
// polling hooks recovering at once) share a single in-flight request instead
// of each firing their own /auth/me call.
let _rehydrateInFlight: Promise<{ username: string } | null> | null = null;

export async function rehydrate(): Promise<{ username: string } | null> {
  if (_rehydrateInFlight) return _rehydrateInFlight;
  _rehydrateInFlight = (async () => {
    try {
      const r = await afetch(`${BASE}/auth/me`);
      if (!r.ok) return null;
      const data = await r.json();
      _username = data.username;
      return { username: data.username };
    } catch {
      return null;
    } finally {
      _rehydrateInFlight = null;
    }
  })();
  return _rehydrateInFlight;
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

  // ---- Builder authoring surface
  // Everything here reads the YAML the Builder currently holds in memory and
  // returns a result; none of it mutates a saved workflow.
  operatorCatalog: () =>
    afetch(`${API}/builder/operators`, { headers: authHeaders() })
      .then(j<OperatorCatalog>),
  /** Typed field tree for the mapping picker and rule editor. Only values that
   *  can actually reach `node_id` are returned. */
  outputContract: (workflow_yaml: string, node_id?: string) =>
    afetch(`${API}/builder/output-contract`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ workflow_yaml, node_id }),
    }).then(j<OutputContract>),
  /** Compile visual schema rows so an invalid row is reported while editing. */
  schemaPreview: (output_fields: FieldSpec[]) =>
    afetch(`${API}/builder/schema-preview`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ output_fields }),
    }).then(j<SchemaPreview>),
  nodeTest: (body: {
    type_name: string;
    config: Record<string, unknown>;
    node_id?: string;
    inputs?: Record<string, unknown>;
    upstream_outputs?: Record<string, unknown>;
    variables?: Record<string, unknown>;
  }) =>
    afetch(`${API}/builder/node-test`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    }).then(j<NodeTestResult>),
  simulateWorkflow: (
    body: {
      workflow_yaml: string;
      inputs?: Record<string, unknown>;
      stub_outputs?: Record<string, Record<string, unknown>>;
      until_node?: string | null;
    },
    signal?: AbortSignal,
  ) =>
    afetch(`${API}/builder/simulate`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
      signal,
    }).then(j<SimulationResult>),
  /** AI proposes schema rows. The result is editable configuration, never an
   *  applied change — the author reviews it in the normal editor. */
  suggestSchema: (body: {
    description: string;
    sample_content?: string;
    existing_fields?: FieldSpec[];
  }) =>
    afetch(`${API}/builder/assist/schema`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    }).then(j<{
      status: string;
      fields: FieldSpec[];
      contract?: string;
      notes?: string;
      message?: string;
    }>),
  suggestRules: (body: {
    description: string;
    available_fields?: Array<Record<string, unknown>>;
  }) =>
    afetch(`${API}/builder/assist/rules`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    }).then(j<{
      status: string;
      rules: BusinessRule[];
      rejected: Array<{ rule: unknown; error: string }>;
      notes?: string;
    }>),
  emailConnections: () =>
    afetch(`${API}/builder/email/connections`, { headers: authHeaders() })
      .then(j<{ connections: EmailConnectionInfo[]; configured: boolean }>),
  /** Not a fetch — a real browser navigation URL (opened via window.open),
   *  since the provider's own consent screen has to render in that window.
   *  Auth travels via the same access_token cookie login already sets, not
   *  a header this navigation could carry. */
  emailConnectUrl: (provider: 'microsoft' | 'gmail') =>
    `${API}/builder/email/connect/${provider}`,
  setEmailConnectionAllowSend: (connectionId: string, allowSend: boolean) =>
    afetch(`${API}/builder/email/connections/${encodeURIComponent(connectionId)}`, {
      method: 'PATCH',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ allow_send: allowSend }),
    }).then(j<{ id: string; allow_send: boolean }>),
  disconnectEmailConnection: (connectionId: string) =>
    afetch(`${API}/builder/email/connections/${encodeURIComponent(connectionId)}`, {
      method: 'DELETE',
      headers: authHeaders(),
    }).then(j<{ id: string; removed: boolean }>),

  // ---- Integrations: cloud storage (Google Drive / OneDrive) connections
  integrationConnections: () =>
    afetch(`${API}/builder/integrations/connections`, { headers: authHeaders() })
      .then(j<{ connections: IntegrationConnectionInfo[]; configured: boolean }>),
  /** Not a fetch — a real browser navigation URL (opened via window.open),
   *  since the provider's own consent screen has to render in that window. */
  integrationConnectUrl: (provider: 'google_drive' | 'onedrive') =>
    `${API}/builder/integrations/connect/${provider}`,
  disconnectIntegrationConnection: (connectionId: string) =>
    afetch(`${API}/builder/integrations/connections/${encodeURIComponent(connectionId)}`, {
      method: 'DELETE',
      headers: authHeaders(),
    }).then(j<{ id: string; removed: boolean }>),
  /** Live folder/search browsing for the node config panel's file picker —
   *  not workflow execution, just a read-only proxy through the connected
   *  account. Presence of `query` selects search vs. plain listing. */
  browseIntegrationFiles: (
    connectionId: string,
    params: { folderId?: string; query?: string; pageSize?: number; pageToken?: string },
  ) => {
    const search = new URLSearchParams();
    if (params.folderId) search.set('folder_id', params.folderId);
    if (params.query) search.set('query', params.query);
    if (params.pageSize) search.set('page_size', String(params.pageSize));
    if (params.pageToken) search.set('page_token', params.pageToken);
    return afetch(
      `${API}/builder/integrations/connections/${encodeURIComponent(connectionId)}/files?${search.toString()}`,
      { headers: authHeaders() },
    ).then(j<{ files: CloudFileMeta[]; next_page_token?: string }>);
  },
  integrationFilePath: (connectionId: string, fileId: string) =>
    afetch(
      `${API}/builder/integrations/connections/${encodeURIComponent(connectionId)}/path/${encodeURIComponent(fileId)}`,
      { headers: authHeaders() },
    ).then(j<{ path: CloudFileMeta[] }>),
  /** Not a fetch — a real browser navigation URL, so the download hits the
   *  browser's native save flow. Auth travels via the same access_token
   *  cookie login already sets, not a header this navigation could carry. */
  downloadIntegrationFileUrl: (connectionId: string, fileId: string) =>
    `${API}/builder/integrations/connections/${encodeURIComponent(connectionId)}/download/${encodeURIComponent(fileId)}`,

  // ---- MCP: business systems reached through configured servers
  mcpServers: () =>
    afetch(`${API}/builder/mcp/servers`, { headers: authHeaders() })
      .then(j<{ servers: MCPServerInfo[]; configured: boolean }>),
  /** Discovered from the server, never hardcoded — a tool added to the MCP
   *  server appears here with no frontend change. */
  mcpTools: (serverId: string, refresh = false) =>
    afetch(
      `${API}/builder/mcp/servers/${encodeURIComponent(serverId)}/tools`
        + (refresh ? '?refresh=true' : ''),
      { headers: authHeaders() },
    ).then(j<{ server_id: string; tools: MCPToolInfo[]; count: number }>),
  mcpHealth: (serverId: string) =>
    afetch(
      `${API}/builder/mcp/servers/${encodeURIComponent(serverId)}/health`,
      { headers: authHeaders() },
    ).then(j<{ server_id: string; healthy: boolean; tool_count: number; error: string | null }>),
  mcpTestTool: (body: {
    server_id: string;
    tool: string;
    arguments?: Record<string, unknown>;
  }) =>
    afetch(`${API}/builder/mcp/test-tool`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    }).then(j<MCPToolTestResult>),
  llmModels: () =>
    afetch(`${API}/llm/models`, { headers: authHeaders() })
      .then(j<{ models: LLMModelInfo[] }>),
  // Live, TTL-cached OpenRouter catalog (~500 models) — searched server-side rather than
  // shipped whole, since the static llmModels() list stays small on purpose.
  llmOpenRouterModels: (query: string, limit = 25) =>
    afetch(
      `${API}/llm/models/openrouter?${new URLSearchParams({
        ...(query ? { q: query } : {}),
        limit: String(limit),
      })}`,
      { headers: authHeaders() },
    ).then(j<{ models: OpenRouterModelInfo[] }>),

  // ---- workflow CRUD
  listWorkflows: () =>
    afetch(`${API}/workflows`, { headers: authHeaders() }).then(j<WorkflowSummary[]>),
  getWorkflow: (name: string) =>
    afetch(`${API}/workflows/by-name/${name}`, { headers: authHeaders() })
      .then(j<{ name: string; yaml: string }>),
  getWorkflowDetail: (name: string) =>
    afetch(`${API}/workflows/${name}/detail`, { headers: authHeaders() })
      .then(j<WorkflowDetail>),
  getWorkflowStats: (name: string) =>
    afetch(`${API}/workflows/${name}/stats`, { headers: authHeaders() })
      .then(j<WorkflowStats>),
  deleteWorkflow: (name: string) =>
    afetch(`${API}/workflows/${name}`, {
      method: 'DELETE',
      headers: authHeaders(),
    }).then(j<{ name: string; deleted: boolean }>),
  saveWorkflow: (name: string, yaml: string) =>
    afetch(`${API}/workflows/save`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ name, yaml }),
    }).then(j<{ ok: true; name: string; version_id: string }>),

  // ---- Builder autosave drafts + immutable versions
  getWorkflowDraft: (name: string) =>
    afetch(`${API}/workflows/${name}/draft`, { headers: authHeaders() })
      .then(j<WorkflowDraft>),
  saveWorkflowDraft: (
    name: string,
    yaml: string,
    canvas?: WorkflowDraft['canvas'],
  ) =>
    afetch(`${API}/workflows/${name}/draft`, {
      method: 'PUT',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ yaml, canvas }),
    }).then(j<WorkflowDraft>),
  deleteWorkflowDraft: (name: string) =>
    afetch(`${API}/workflows/${name}/draft`, {
      method: 'DELETE',
      headers: authHeaders(),
    }).then(j<{ ok: boolean }>),
  listWorkflowVersions: (name: string) =>
    afetch(`${API}/workflows/${name}/versions`, { headers: authHeaders() })
      .then(j<WorkflowVersionSummary[]>),
  getWorkflowVersion: (name: string, versionId: string) =>
    afetch(`${API}/workflows/${name}/versions/${versionId}`, { headers: authHeaders() })
      .then(j<{ yaml: string }>),
  restoreWorkflowVersion: (name: string, versionId: string) =>
    afetch(`${API}/workflows/${name}/versions/${versionId}/restore`, {
      method: 'POST',
      headers: authHeaders(),
    }).then(j<{ yaml: string; version_id: string }>),
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
  autofixWorkflow: (
    workflow_yaml: string,
    inputs?: Record<string, unknown>,
    check_services = false,
  ) =>
    afetch(`${API}/workflows/autofix`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ workflow_yaml, inputs, check_services }),
    }).then(j<AutofixWorkflowResult>),
  generateWorkflow: (prompt: string, sample_inputs?: Record<string, unknown>) =>
    afetch(`${API}/workflows/generate`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ prompt, sample_inputs }),
    }).then(j<GenerateWorkflowResult>),

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
      .then(j<RunCostSummary>),

  // ---- Cost Management (admin) — app/api/cost_admin.py
  costAdminOverview: (days = 30) =>
    afetch(`${API}/cost-admin/overview?days=${days}`, { headers: authHeaders() })
      .then(j<CostOverview>),
  costAdminPricing: (openrouterQuery = '', openrouterLimit = 25) =>
    afetch(
      `${API}/cost-admin/pricing?${new URLSearchParams({
        ...(openrouterQuery ? { openrouter_q: openrouterQuery } : {}),
        openrouter_limit: String(openrouterLimit),
      })}`,
      { headers: authHeaders() },
    ).then(j<PricingResponse>),
  setPricingOverride: (model: string, body: { input_usd_per_1k: number; output_usd_per_1k: number }) =>
    afetch(`${API}/cost-admin/pricing/${encodeURIComponent(model)}`, {
      method: 'PUT',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    }).then(j<{ model: string; status: string }>),
  clearPricingOverride: (model: string) =>
    afetch(`${API}/cost-admin/pricing/${encodeURIComponent(model)}`, {
      method: 'DELETE',
      headers: authHeaders(),
    }).then(j<{ model: string; status: string }>),
  costAdminInfraAllocations: () =>
    afetch(`${API}/cost-admin/infra-allocations`, { headers: authHeaders() })
      .then(j<{ models: InfraAllocationEntry[] }>),
  setInfraAllocation: (
    model: string,
    body: { allocation_type: 'per_call' | 'monthly_amortized'; value_usd: number; expected_monthly_calls?: number | null },
  ) =>
    afetch(`${API}/cost-admin/infra-allocations/${encodeURIComponent(model)}`, {
      method: 'PUT',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    }).then(j<{ model: string; status: string }>),
  costAdminCacheSummary: (sinceDays = 30) =>
    afetch(`${API}/cost-admin/cache-summary?since_days=${sinceDays}`, { headers: authHeaders() })
      .then(j<CacheSummary>),
  costAdminBudgets: () =>
    afetch(`${API}/cost-admin/budgets`, { headers: authHeaders() })
      .then(j<BudgetsResponse>),
  setGlobalBudget: (daily_limit_usd: number) =>
    afetch(`${API}/cost-admin/budgets/global`, {
      method: 'PUT',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ daily_limit_usd }),
    }).then(j<{ scope: string; daily_limit_usd: number }>),
  setSessionBudget: (sessionId: string, daily_limit_usd: number) =>
    afetch(`${API}/cost-admin/budgets/session/${encodeURIComponent(sessionId)}`, {
      method: 'PUT',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ daily_limit_usd }),
    }).then(j<{ session_id: string; daily_limit_usd: number }>),
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
  runHistory: (limit?: number) =>
    afetch(`${API}/runs/mine${limit != null ? `?limit=${limit}` : ''}`, { headers: authHeaders() })
      .then(j<{ count: number; runs: RunSummary[] }>),

  runDetail: (run_id: string) =>
    afetch(`${API}/runs/mine/${run_id}`, { headers: authHeaders() })
      .then(j<{ run: RunDetail; audit: AuditEvent[] }>),
  runCandidates: (run_id: string) =>
    afetch(`${API}/candidates/${run_id}`, { headers: authHeaders() })
      .then(j<{
        run_id: string;
        count: number;
        candidates: RunCandidate[];
        discovered_count: number;
        discovered_candidates: DiscoveredCandidate[];
        internal_evidence_count: number;
        internal_evidence: InternalEvidenceRecord[];
      }>),
  verifyClaim: (run_id: string, record_id: string) =>
    afetch(`${API}/candidates/${run_id}/verify-claim`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ record_id }),
    }).then(j<{ record_id: string; result: ClaimVerificationResult }>),
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
  businessProjection: (run_id: string) =>
    afetch(`${API}/runs/mine/${run_id}/business-projection`, { headers: authHeaders() })
      .then(j<BusinessProjection>),
  // Narration is a separate POST because it may cost a model call. The
  // projection renders fully without it; this only improves the wording.
  businessNarration: (run_id: string) =>
    afetch(`${API}/runs/mine/${run_id}/business-narration`, {
      method: 'POST',
      headers: authHeaders(),
    }).then(j<BusinessNarration>),
  businessExplanation: (run_id: string) =>
    afetch(`${API}/runs/mine/${run_id}/business-explanation`, { headers: authHeaders() })
      .then(j<BusinessExplanation>),
  businessTechnicalDetail: (run_id: string, activity_id: string) =>
    afetch(`${API}/runs/mine/${run_id}/business-technical/${activity_id}`, { headers: authHeaders() })
      .then(j<BusinessTechnicalDetail>),
  businessAction: (run_id: string, type: string, params: Record<string, unknown> = {}) =>
    afetch(`${API}/runs/mine/${run_id}/business-action`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ type, params }),
    }).then(j<BusinessActionResult>),
  assignRun: (run_id: string, assignee: string) =>
    afetch(`${API}/runs/mine/${run_id}/assign`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ assignee }),
    }).then(j<{ ok: boolean; assigned_to: string }>),
  correctFact: (run_id: string, field: string, value: unknown) =>
    afetch(`${API}/runs/mine/${run_id}/fact-correction`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ field, value }),
    }).then(j<{ ok: boolean; edit: { field: string; value: unknown; stale_decisions: string[]; edited_at: string } }>),
  pendingGate: (run_id: string) =>
    afetch(`${API}/runs/mine/${run_id}/pending-gate`, { headers: authHeaders() })
      .then(j<PendingGate>),
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
  runChatHistory: (run_id: string) =>
    afetch(`${API}/runs/mine/${run_id}/chat`, { headers: authHeaders() })
      .then(j<{ turns: RunChatTurn[]; starter_questions: string[] }>),
  askAboutRun: (run_id: string, question: string, history: RunChatTurn[] = []) =>
    afetch(`${API}/runs/mine/${run_id}/chat`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({
        question,
        history: history.map(({ role, content }) => ({ role, content })),
      }),
    }).then(j<{ turns: RunChatTurn[]; answer: string }>),
  askAboutNodeTypes: (
    question: string,
    focus_type_name?: string,
    history: RunChatTurn[] = [],
    context?: AskContext,
  ) =>
    afetch(`${API}/node-types/ask`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({
        question,
        focus_type_name,
        history: history.map(({ role, content }) => ({ role, content })),
        context,
      }),
    }).then(j<{ answer: string }>),
  draftPrompt: (
    type_name: string,
    field_name: string,
    instruction: string,
    history: RunChatTurn[] = [],
  ) =>
    afetch(`${API}/node-types/draft-prompt`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({
        type_name,
        field_name,
        instruction,
        history: history.map(({ role, content }) => ({ role, content })),
      }),
    }).then(j<{ answer: string }>),
  draftInstructions: (body: {
    existing_instructions: string;
    input_fields: DraftInstructionsField[];
    output_fields: DraftInstructionsField[];
  }) =>
    afetch(`${API}/node-types/draft-instructions`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    }).then(j<{ answer: string }>),
  draftCode: (body: DraftCodeRequest) =>
    afetch(`${API}/node-types/draft-code`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    }).then(j<{ answer: string }>),

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
    stage_run_id?: string,
  ) =>
    afetch(`${API}/pipelines/run`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ pipeline_yaml, inputs, session_id, pipeline_run_id, stage_run_id }),
    }).then(j<PipelineStageOutcome>),
  advancePipeline: (pipeline_run_id: string, session_id?: string, stage_run_id?: string) =>
    afetch(`${API}/pipelines/${pipeline_run_id}/advance`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ session_id, stage_run_id }),
    }).then(j<PipelineStageOutcome>),
  pipelineRuns: () =>
    afetch(`${API}/pipelines/mine`, { headers: authHeaders() })
      .then(j<{ count: number; runs: PipelineRunSummary[] }>),
  pipelineRunDetail: (pipeline_run_id: string) =>
    afetch(`${API}/pipelines/mine/${pipeline_run_id}`, { headers: authHeaders() })
      .then(j<PipelineRunDetail>),
  abandonPipeline: (pipeline_run_id: string, session_id?: string) =>
    afetch(`${API}/pipelines/${pipeline_run_id}/abandon`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ session_id }),
    }).then(j<{ pipeline_run_id: string; status: string }>),

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

  workflowGoldenSet: (name = 'verder_customer_triage') =>
    afetch(`${API}/eval/workflow-golden-set?name=${encodeURIComponent(name)}`, { headers: authHeaders() })
      .then(j<{ name: string; n: number; cases: { id: string; label: string; expected: Record<string, unknown> }[] }>),

  workflowCompare: (golden_set: string, models: string[]) =>
    afetch(`${API}/eval/workflow-compare`, {
      method: 'POST',
      headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ golden_set, models }),
    }).then(j<WorkflowCompareResponse>),

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